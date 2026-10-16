/**
 * /api/system/stats: this box's own telemetry.
 *
 * A SAME-ORIGIN NEXT ROUTE, not a backend endpoint: it reads the machine's own
 * sysfs/procfs and is therefore not in the proxy allowlist and carries no key.
 *
 * Names no hardware. Memory, temperature and uptime are portable across Linux;
 * the GPU-load and rail-power files below exist on some boards (they were
 * verified on a Jetson Orin) and not others. A missing file is not an error —
 * `available: false` is the answer, and the UI says so in words instead of
 * drawing a zero that reads like a measurement.
 *
 * Paths read where present:
 *   gpu    /sys/devices/gpu.0/load            (0-1000 → %)
 *   mem    /proc/meminfo                       (MemTotal/MemAvailable → GB)
 *   temp   /sys/class/thermal/thermal_zone*    (max of cpu/gpu/soc, m°C)
 *   power  ina3221 hwmon in{n}_input (mV) × curr{n}_input (mA) → W
 *   uptime /proc/uptime
 *
 * Keeps a module-level ring buffer (~120 samples ≈ 2 h at one per minute)
 * of { t, gpu } for the Device page's last-hour chart. The buffer is fed by
 * a lazy 60 s self-sample timer plus each poll (throttled to ≥55 s apart so
 * fast 4 s polling doesn't shrink the window). Stored on globalThis so dev
 * HMR doesn't spawn duplicate timers.
 *
 * On Windows/dev or any read failure: `available: false`, zeroed values —
 * the UI shows honest placeholders, never fake numbers. No secrets here;
 * same-origin local telemetry only.
 */
import { promises as fs } from "fs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Sample = { t: number; gpu: number };

const G = globalThis as unknown as {
  __puffStatsHistory?: Sample[];
  __puffStatsTimer?: ReturnType<typeof setInterval>;
  __puffStatsLastPush?: number;
};

const HISTORY_MAX = 120;
const PUSH_MIN_MS = 55_000;

/* ── low-level readers (every one returns null on failure) ─────────── */

async function readNum(path: string): Promise<number | null> {
  try {
    const raw = await fs.readFile(path, "utf8");
    const v = parseFloat(raw.trim());
    return Number.isFinite(v) ? v : null;
  } catch {
    return null;
  }
}

async function readGpuPct(): Promise<number | null> {
  // Verified on the Orin: 0-1000 integer.
  const v = await readNum("/sys/devices/gpu.0/load");
  if (v !== null) return clamp(v / 10, 0, 100);
  try {
    const entries = await fs.readdir("/sys/devices/platform");
    for (const e of entries) {
      if (!/gpu/i.test(e)) continue;
      const alt = await readNum(`/sys/devices/platform/${e}/load`);
      if (alt !== null) return clamp(alt / 10, 0, 100);
    }
  } catch {
    /* no fallback available */
  }
  return null;
}

async function readMem(): Promise<{ usedGb: number; totalGb: number } | null> {
  try {
    const raw = await fs.readFile("/proc/meminfo", "utf8");
    const grab = (key: string) => {
      const m = raw.match(new RegExp(`^${key}:\\s+(\\d+)\\s*kB`, "m"));
      return m ? parseInt(m[1], 10) : NaN;
    };
    const totalKb = grab("MemTotal");
    const availKb = grab("MemAvailable");
    if (!Number.isFinite(totalKb) || !Number.isFinite(availKb)) return null;
    const totalGb = totalKb / 1048576;
    return { usedGb: round1(totalGb - availKb / 1048576), totalGb: round1(totalGb) };
  } catch {
    return null;
  }
}

async function readTempC(): Promise<number | null> {
  try {
    const zones = await fs.readdir("/sys/class/thermal");
    let max: number | null = null;
    for (const z of zones) {
      if (!/^thermal_zone\d+$/.test(z)) continue;
      try {
        const type = (
          await fs.readFile(`/sys/class/thermal/${z}/type`, "utf8")
        ).trim();
        if (!/cpu|gpu|soc/i.test(type)) continue;
        const milli = await readNum(`/sys/class/thermal/${z}/temp`);
        // Millidegrees; ignore obviously bogus sensors.
        if (milli === null || milli < -40_000 || milli > 150_000) continue;
        const c = milli / 1000;
        if (max === null || c > max) max = c;
      } catch {
        /* skip zone */
      }
    }
    return max;
  } catch {
    return null;
  }
}

async function readPowerW(): Promise<number | null> {
  const base = "/sys/bus/i2c/drivers/ina3221";
  try {
    const devs = await fs.readdir(base);
    let total = 0;
    let found = false;
    for (const dev of devs) {
      // Device dirs look like "1-0040"; skip bind/unbind/module entries.
      if (!/^\d+-[0-9a-f]+$/i.test(dev)) continue;
      let hwmons: string[] = [];
      try {
        hwmons = await fs.readdir(`${base}/${dev}/hwmon`);
      } catch {
        continue;
      }
      for (const hw of hwmons) {
        if (!/^hwmon\d+$/.test(hw)) continue;
        const dir = `${base}/${dev}/hwmon/${hw}`;
        let files: string[] = [];
        try {
          files = await fs.readdir(dir);
        } catch {
          continue;
        }
        let chipFound = false;
        // Verified on the Orin: in{n}_input is mV, curr{n}_input is mA.
        for (const f of files) {
          const m = f.match(/^in(\d+)_input$/);
          if (!m || !files.includes(`curr${m[1]}_input`)) continue;
          const mv = await readNum(`${dir}/${f}`);
          const ma = await readNum(`${dir}/curr${m[1]}_input`);
          if (mv === null || ma === null) continue;
          total += (mv * ma) / 1e6;
          chipFound = true;
        }
        if (!chipFound) {
          // Fallback: some kernels expose power{n}_input in µW directly.
          for (const f of files) {
            if (!/^power\d+_input$/.test(f)) continue;
            const uw = await readNum(`${dir}/${f}`);
            if (uw === null) continue;
            total += uw / 1e6;
            chipFound = true;
          }
        }
        found = found || chipFound;
      }
    }
    return found ? total : null;
  } catch {
    return null;
  }
}

async function readUptimeSec(): Promise<number | null> {
  try {
    const raw = await fs.readFile("/proc/uptime", "utf8");
    const v = parseFloat(raw.split(/\s+/)[0]);
    return Number.isFinite(v) ? Math.floor(v) : null;
  } catch {
    return null;
  }
}

/* ── ring buffer + self-sampler ────────────────────────────────────── */

function pushSample(gpuPct: number) {
  const now = Date.now();
  if (now - (G.__puffStatsLastPush ?? 0) < PUSH_MIN_MS) return;
  G.__puffStatsLastPush = now;
  const hist = (G.__puffStatsHistory ??= []);
  hist.push({ t: now, gpu: round1(gpuPct) });
  if (hist.length > HISTORY_MAX) hist.splice(0, hist.length - HISTORY_MAX);
}

function ensureSampler() {
  if (G.__puffStatsTimer || process.platform !== "linux") return;
  G.__puffStatsTimer = setInterval(async () => {
    const gpu = await readGpuPct();
    if (gpu !== null) pushSample(gpu);
  }, 60_000);
  // Don't keep the process alive just for telemetry.
  (G.__puffStatsTimer as unknown as { unref?: () => void }).unref?.();
}

/* ── helpers ───────────────────────────────────────────────────────── */

const clamp = (v: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, v));
const round1 = (v: number) => Math.round(v * 10) / 10;

/* ── route ─────────────────────────────────────────────────────────── */

export async function GET() {
  ensureSampler();

  const [gpu, mem, tempC, powerW, uptimeSec] = await Promise.all([
    readGpuPct(),
    readMem(),
    readTempC(),
    readPowerW(),
    readUptimeSec(),
  ]);

  const available = gpu !== null || mem !== null;
  if (gpu !== null) pushSample(gpu);

  return Response.json({
    available,
    gpu: { pct: gpu !== null ? round1(gpu) : 0 },
    mem: {
      usedGb: mem?.usedGb ?? 0,
      totalGb: mem?.totalGb ?? 0,
    },
    tempC: tempC !== null ? round1(tempC) : 0,
    powerW: powerW !== null ? round1(powerW) : 0,
    uptimeSec: uptimeSec ?? 0,
    history: G.__puffStatsHistory ?? [],
  });
}
