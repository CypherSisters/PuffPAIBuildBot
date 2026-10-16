/* FitFam — GENERATED FILE, do not edit by hand. Edit fitfam-app.jsx.
 *
 * NOTE: the esbuild invocation below is the recorded one, but the version
 * that produced this file wrapped the module in a __commonJS shim and a
 * current esbuild does not. Until somebody pins the version, a change to the
 * .jsx has to be mirrored here by hand rather than rebuilt, or the whole file
 * changes shape for a one-line edit. The demo users are deliberately two
 * ageless adults with generic names: a fixture that reads like somebody's
 * actual family is a privacy leak in every screenshot of this app.
 *
 * The page used to pull React, ReactDOM and babel-standalone from
 * cdnjs.cloudflare.com and transpile its JSX in the browser: three requests to
 * a third party on every open of an app that is supposed to run with no cloud
 * at all. The JSX now lives in fitfam-app.jsx and is compiled here once:
 *
 *   esbuild fitfam-app.jsx --jsx=transform --format=iife --target=es2018 \
 *           --outfile=fitfam-app.js
 *
 * React 18.2.0 UMD production builds are vendored in vendor/react/.
 */
(() => {
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __commonJS = (cb, mod) => function __require() {
    try {
      return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
    } catch (e) {
      throw mod = 0, e;
    }
  };
  var require_fitfam = __commonJS({
    "fitfam.jsx"() {
      const { useState, useEffect, useCallback, useRef, useMemo } = React;
      const EXERCISES = {
        calisthenics: [
          { id: "c1", name: "Push-Ups", muscle: "chest", levels: { easy: "3\xD710 (knees ok)", mid: "4\xD715 standard", challenging: "4\xD712 archer push-ups" }, time: 5, desc: "Fundamental upper push. Engage core, full ROM." },
          { id: "c2", name: "Diamond Push-Ups", muscle: "triceps", levels: { easy: "3\xD78", mid: "4\xD712", challenging: "4\xD715 tempo 3-1-3" }, time: 5, desc: "Hands close together targeting triceps. Elbows tight." },
          { id: "c3", name: "Pike Push-Ups", muscle: "shoulders", levels: { easy: "3\xD78", mid: "4\xD710", challenging: "4\xD712 elevated feet" }, time: 5, desc: "Handstand push-up progression. Hips high, head to floor." },
          { id: "c4", name: "Bodyweight Squats", muscle: "legs", levels: { easy: "3\xD715", mid: "4\xD720", challenging: "4\xD715 jump squats" }, time: 5, desc: "Full depth, knees tracking toes. Drive through heels." },
          { id: "c5", name: "Pistol Squat Progression", muscle: "legs", levels: { easy: "3\xD75/side assisted", mid: "3\xD78/side to box", challenging: "4\xD78/side full pistol" }, time: 6, desc: "Unilateral leg strength. Use support as needed." },
          { id: "c6", name: "Bulgarian Split Squats", muscle: "legs", levels: { easy: "3\xD78/side", mid: "3\xD712/side", challenging: "4\xD712/side tempo 4-0-1" }, time: 6, desc: "Rear foot elevated. Deep stretch on hip flexors." },
          { id: "c7", name: "Dips (Bench/Parallel)", muscle: "chest", levels: { easy: "3\xD78 bench", mid: "3\xD712 bench", challenging: "4\xD710 parallel bars" }, time: 5, desc: "Upper body push. Lean forward for chest emphasis." },
          { id: "c8", name: "Pull-Up / Inverted Rows", muscle: "back", levels: { easy: "3\xD78 inverted rows", mid: "3\xD78 pull-ups", challenging: "4\xD710 pull-ups" }, time: 6, desc: "Scapular retraction, full hang to chin over bar." },
          { id: "c9", name: "Chin-Ups", muscle: "back", levels: { easy: "3\xD75 negatives", mid: "3\xD78", challenging: "4\xD710 weighted" }, time: 6, desc: "Supinated grip. Bicep and lat emphasis." },
          { id: "c10", name: "Hollow Body Hold", muscle: "core", levels: { easy: "3\xD720s", mid: "3\xD730s", challenging: "3\xD745s" }, time: 4, desc: "Lower back pressed flat. Arms and legs extended." },
          { id: "c11", name: "L-Sit Progression", muscle: "core", levels: { easy: "3\xD710s tucked", mid: "3\xD715s one leg", challenging: "3\xD720s full L-sit" }, time: 5, desc: "Straight arms on parallettes or floor. Compress." },
          { id: "c12", name: "Plank Variations", muscle: "core", levels: { easy: "3\xD730s standard", mid: "3\xD745s side planks", challenging: "3\xD760s plank to push-up" }, time: 5, desc: "Rigid body line. Anti-extension core training." },
          { id: "c13", name: "Handstand Wall Hold", muscle: "shoulders", levels: { easy: "3\xD715s chest to wall", mid: "3\xD730s", challenging: "3\xD745s freestanding attempts" }, time: 6, desc: "Fingers spread, push through shoulders. Stack joints." },
          { id: "c14", name: "Muscle-Up Progression", muscle: "back", levels: { easy: "3\xD75 explosive pull-ups", mid: "3\xD73 transition practice", challenging: "3\xD75 muscle-ups" }, time: 7, desc: "Pull high, aggressive kip to transition over bar." },
          { id: "c15", name: "Burpees", muscle: "full", levels: { easy: "3\xD78", mid: "4\xD712", challenging: "5\xD715" }, time: 5, desc: "Full body metabolic conditioning. Chest to floor." }
        ],
        kettlebell: [
          { id: "k1", name: "Kettlebell Swings", muscle: "posterior", levels: { easy: "3\xD712 Russian", mid: "4\xD715 Russian", challenging: "5\xD715 American" }, time: 5, desc: "Hip hinge power. Snap hips, float the bell." },
          { id: "k2", name: "Turkish Get-Up", muscle: "full", levels: { easy: "2\xD73/side", mid: "3\xD73/side", challenging: "3\xD75/side" }, time: 8, desc: "7-step sequence from floor to standing." },
          { id: "k3", name: "Goblet Squat", muscle: "legs", levels: { easy: "3\xD710", mid: "4\xD712", challenging: "4\xD715 with 3s pause" }, time: 5, desc: "Hold KB at chest. Deep squat, elbows between knees." },
          { id: "k4", name: "Single-Arm Press", muscle: "shoulders", levels: { easy: "3\xD76/side", mid: "3\xD78/side", challenging: "4\xD710/side" }, time: 5, desc: "Strict press from rack. Core braced, no lean." },
          { id: "k5", name: "KB Clean", muscle: "full", levels: { easy: "3\xD76/side", mid: "3\xD78/side", challenging: "4\xD710/side" }, time: 5, desc: "Hip-driven pull to rack position. Absorb at chest." },
          { id: "k6", name: "KB Snatch", muscle: "full", levels: { easy: "3\xD75/side", mid: "3\xD78/side", challenging: "4\xD710/side" }, time: 6, desc: "One motion floor to overhead. High pull then punch through." },
          { id: "k7", name: "Windmill", muscle: "core", levels: { easy: "3\xD75/side bodyweight", mid: "3\xD75/side with KB", challenging: "3\xD78/side with KB" }, time: 6, desc: "Lateral hip hinge. KB locked overhead." },
          { id: "k8", name: "KB Halo", muscle: "shoulders", levels: { easy: "3\xD78 each way", mid: "3\xD712 each way", challenging: "4\xD712 each way" }, time: 4, desc: "Circle KB around head. Shoulder mobility and stability." },
          { id: "k9", name: "Farmer Carry (Single)", muscle: "core", levels: { easy: "3\xD730s/side", mid: "3\xD745s/side", challenging: "3\xD760s/side" }, time: 5, desc: "Anti-lateral flexion. Walk tall, engage obliques." },
          { id: "k10", name: "KB Deadlift", muscle: "posterior", levels: { easy: "3\xD710", mid: "4\xD712", challenging: "4\xD715 single-leg" }, time: 5, desc: "Hip hinge, flat back. Drive through heels to stand." },
          { id: "k11", name: "KB Thruster", muscle: "full", levels: { easy: "3\xD78", mid: "4\xD710", challenging: "4\xD712" }, time: 5, desc: "Goblet squat into overhead press. Full body power." },
          { id: "k12", name: "KB Row", muscle: "back", levels: { easy: "3\xD78/side", mid: "4\xD710/side", challenging: "4\xD712/side tempo" }, time: 5, desc: "Bent over, pull to hip. Squeeze scapula." }
        ],
        rom: [
          { id: "r1", name: "Sun Salutation A Flow", muscle: "full", levels: { easy: "3 rounds slow", mid: "5 rounds", challenging: "8 rounds with holds" }, time: 6, desc: "Classic vinyasa sequence. Breath-synchronized movement." },
          { id: "r2", name: "Pigeon Pose (Yin Hold)", muscle: "hips", levels: { easy: "60s/side", mid: "90s/side", challenging: "3min/side" }, time: 5, desc: "Deep hip external rotation. Relax into gravity, breathe." },
          { id: "r3", name: "90/90 Hip Switches", muscle: "hips", levels: { easy: "2\xD78 switches", mid: "3\xD710 with holds", challenging: "3\xD710 with lift-offs" }, time: 5, desc: "Internal and external rotation. Functional hip mobility." },
          { id: "r4", name: "Cat-Cow Spinal Waves", muscle: "spine", levels: { easy: "2\xD710", mid: "3\xD712 segmental", challenging: "3\xD715 with rotation" }, time: 4, desc: "Spinal flexion/extension. Breathe into each segment." },
          { id: "r5", name: "Thread the Needle", muscle: "spine", levels: { easy: "2\xD78/side", mid: "3\xD710/side", challenging: "3\xD710/side with hold" }, time: 4, desc: "Thoracic rotation from quadruped. Reach through and open." },
          { id: "r6", name: "Shoulder CARs", muscle: "shoulders", levels: { easy: "2\xD75/side", mid: "3\xD75/side", challenging: "3\xD78/side with tension" }, time: 4, desc: "Controlled Articular Rotations. Full circle, max intent." },
          { id: "r7", name: "Deep Squat Hold", muscle: "hips", levels: { easy: "3\xD730s", mid: "3\xD760s", challenging: "3\xD790s with reaches" }, time: 5, desc: "Full depth squat. Heels down, chest up." },
          { id: "r8", name: "Forward Fold + Ragdoll", muscle: "hamstrings", levels: { easy: "60s gentle", mid: "90s with PNF", challenging: "2min contract-relax" }, time: 4, desc: "Hamstring and spine release. Bend knees if needed." },
          { id: "r9", name: "Downward Dog to Cobra Flow", muscle: "full", levels: { easy: "3\xD75", mid: "3\xD78", challenging: "3\xD710 with holds" }, time: 5, desc: "Alternating inversion and extension. Full chain mobility." },
          { id: "r10", name: "Hip Flexor Lunge Stretch", muscle: "hips", levels: { easy: "60s/side", mid: "90s/side with reach", challenging: "2min/side with PAILs" }, time: 5, desc: "Half-kneeling. Drive hip forward, squeeze glute." },
          { id: "r11", name: "Ankle Dorsiflexion Mobilization", muscle: "ankles", levels: { easy: "2\xD710/side", mid: "3\xD712/side", challenging: "3\xD712/side loaded" }, time: 4, desc: "Knee over toe drive. Wall-supported ankle stretch." },
          { id: "r12", name: "Supine Spinal Twist", muscle: "spine", levels: { easy: "60s/side", mid: "90s/side", challenging: "2min/side" }, time: 5, desc: "Knees stacked, arms wide. Decompress and rotate." },
          { id: "r13", name: "Frog Stretch", muscle: "hips", levels: { easy: "60s", mid: "90s with rocks", challenging: "2min deep hold" }, time: 4, desc: "Wide knees, hips sinking. Adductor and hip opener." },
          { id: "r14", name: "Box Breathing + Body Scan", muscle: "recovery", levels: { easy: "3min", mid: "5min", challenging: "8min" }, time: 5, desc: "4-4-4-4 breath cycle. Parasympathetic activation." },
          { id: "r15", name: "Wrist Circles & Extensions", muscle: "wrists", levels: { easy: "2\xD710 each", mid: "3\xD710 each", challenging: "3\xD715 loaded" }, time: 3, desc: "Essential for calisthenics prehab. All directions." }
        ],
        gym: [
          { id: "g1", name: "Barbell Back Squat", muscle: "legs", levels: { easy: "3\xD78 light", mid: "4\xD78 moderate", challenging: "5\xD75 heavy" }, time: 8, desc: "Foundational leg strength. Full depth, braced core." },
          { id: "g2", name: "Bench Press", muscle: "chest", levels: { easy: "3\xD78 light", mid: "4\xD78 moderate", challenging: "5\xD75 heavy" }, time: 7, desc: "Upper body horizontal push. Arch, retract scapulae." },
          { id: "g3", name: "Bent-Over Row", muscle: "back", levels: { easy: "3\xD710", mid: "4\xD710", challenging: "4\xD78 heavy" }, time: 6, desc: "Pull to navel. Flat back, controlled eccentric." },
          { id: "g4", name: "Overhead Press", muscle: "shoulders", levels: { easy: "3\xD78", mid: "4\xD78", challenging: "4\xD76 heavy" }, time: 6, desc: "Strict press overhead. Core braced, full lockout." },
          { id: "g5", name: "Deadlift", muscle: "posterior", levels: { easy: "3\xD78 light", mid: "4\xD76 moderate", challenging: "5\xD73 heavy" }, time: 8, desc: "Hip hinge from floor. Full body posterior chain." },
          { id: "g6", name: "Leg Press", muscle: "legs", levels: { easy: "3\xD712", mid: "4\xD710", challenging: "4\xD78 heavy" }, time: 6, desc: "Quad/glute focus. Full ROM, don't lock knees." },
          { id: "g7", name: "Lat Pulldown", muscle: "back", levels: { easy: "3\xD710", mid: "4\xD710", challenging: "4\xD78 heavy" }, time: 5, desc: "Vertical pull. Wide grip, squeeze lats at bottom." },
          { id: "g8", name: "Dumbbell Shoulder Press", muscle: "shoulders", levels: { easy: "3\xD710", mid: "4\xD710", challenging: "4\xD78 heavy" }, time: 5, desc: "Shoulder stability and strength. Controlled path." }
        ]
      };
      const WORKOUT_TYPES = {
        alex: [
          { id: "calisthenics", label: "Calisthenics", emoji: "\u{1F938}", pool: ["calisthenics"] },
          { id: "kettlebell", label: "Kettlebell", emoji: "\u{1F3CB}\uFE0F", pool: ["kettlebell"] },
          { id: "rom", label: "Range of Motion", emoji: "\u{1F9D8}", pool: ["rom"] },
          { id: "cali-kb", label: "Calisthenics + KB", emoji: "\u{1F4AA}", pool: ["calisthenics", "kettlebell"] },
          { id: "full", label: "Full Session", emoji: "\u26A1", pool: ["calisthenics", "kettlebell", "rom"] }
        ],
        sam: [
          { id: "rom", label: "Flexibility", emoji: "\u{1F9D8}", pool: ["rom"] },
          { id: "gym", label: "Gym Strength", emoji: "\u{1F3CB}\uFE0F", pool: ["gym"] },
          { id: "full", label: "Full Training", emoji: "\u26A1", pool: ["rom", "gym"] }
        ]
      };
      const TIME_CONFIGS = { 15: { exercises: 3 }, 30: { exercises: 5 }, 45: { exercises: 7 }, 60: { exercises: 9 } };
      const DEFAULT_GOALS = {
        alex: [
          { id: "mg1", text: "Hold 60s L-sit", target: 60, unit: "seconds", current: 0 },
          { id: "mg2", text: "10 pistol squats per leg", target: 10, unit: "reps", current: 0 },
          { id: "mg3", text: "Touch palms to floor (forward fold)", target: 100, unit: "% ROM", current: 0 },
          { id: "mg4", text: "30-day workout streak", target: 30, unit: "days", current: 0 }
        ],
        sam: [
          { id: "rg1", text: "Hold a 2-minute plank", target: 120, unit: "seconds", current: 0 },
          { id: "rg2", text: "Deadlift bodyweight for 5 reps", target: 5, unit: "reps", current: 0 },
          { id: "rg3", text: "Full splits (front)", target: 100, unit: "% ROM", current: 0 },
          { id: "rg4", text: "Train 4x/week for 8 weeks", target: 32, unit: "sessions", current: 0 }
        ]
      };
      function generateWorkout(userId, typeId, level, duration) {
        var _a;
        const types = WORKOUT_TYPES[userId];
        const type = types.find((t) => t.id === typeId);
        if (!type) return [];
        const count = ((_a = TIME_CONFIGS[duration]) == null ? void 0 : _a.exercises) || 5;
        let pool = [];
        type.pool.forEach((cat) => {
          const exs = EXERCISES[cat] || [];
          pool.push(...exs);
        });
        const shuffled = [...pool].sort(() => Math.random() - 0.5);
        const selected = [];
        const usedMuscles = /* @__PURE__ */ new Set();
        for (const ex of shuffled) {
          if (selected.length >= count) break;
          if (usedMuscles.size < count - 1 && usedMuscles.has(ex.muscle) && shuffled.length > count) continue;
          selected.push({ ...ex, prescription: ex.levels[level] });
          usedMuscles.add(ex.muscle);
        }
        if (selected.length < count) {
          for (const ex of shuffled) {
            if (selected.length >= count) break;
            if (!selected.find((s) => s.id === ex.id)) {
              selected.push({ ...ex, prescription: ex.levels[level] });
            }
          }
        }
        return selected;
      }
      const LS_KEY = "fitfam_data_v1";
      const SYNC_API = "/api/pai/apps/fitfam";
      let _useLocalStorage = false;
      async function loadData() {
        try {
          const res = await fetch(SYNC_API + "/data");
          const data = await res.json();
          try {
            localStorage.setItem(LS_KEY, JSON.stringify(data));
          } catch (e) {
          }
          return data;
        } catch (e) {
          _useLocalStorage = true;
          try {
            const raw = localStorage.getItem(LS_KEY);
            if (raw) return JSON.parse(raw);
          } catch (e2) {
          }
          return null;
        }
      }
      async function saveData(data) {
        try {
          localStorage.setItem(LS_KEY, JSON.stringify(data));
        } catch (e) {
        }
        if (_useLocalStorage) return;
        try {
          await fetch(SYNC_API + "/data", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
          });
        } catch (e) {
          _useLocalStorage = true;
          console.warn("Server unreachable, using localStorage");
        }
      }
      function getDefaultData() {
        return {
          users: {
            alex: {
              name: "Alex",
              emoji: "\u{1F9D4}",
              prefs: { level: "mid", type: "full", duration: 45 },
              goals: DEFAULT_GOALS.alex,
              history: [],
              streak: 0,
              totalWorkouts: 0
            },
            sam: {
              name: "Sam",
              emoji: "\{1F3C3}",
              prefs: { level: "mid", type: "gym", duration: 30 },
              goals: DEFAULT_GOALS.sam,
              history: [],
              streak: 0,
              totalWorkouts: 0
            }
          }
        };
      }
      const S = {
        bg: "linear-gradient(135deg, #0a1628 0%, #0c2340 15%, #0f3460 30%, #1a6b8a 50%, #2dabb5 65%, #72d6c9 80%, #a8edea 90%, #d4f5e6 100%)",
        glass: {
          background: "rgba(255,255,255,0.12)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          border: "1px solid rgba(255,255,255,0.25)",
          borderRadius: 20
        },
        glassLight: {
          background: "rgba(255,255,255,0.2)",
          backdropFilter: "blur(16px)",
          WebkitBackdropFilter: "blur(16px)",
          border: "1px solid rgba(255,255,255,0.3)",
          borderRadius: 16
        },
        glossyBtn: (color = "#2dabb5") => ({
          background: `linear-gradient(180deg, ${color}dd 0%, ${color} 40%, ${color}bb 100%)`,
          border: "1px solid rgba(255,255,255,0.3)",
          borderRadius: 14,
          color: "white",
          fontWeight: 700,
          cursor: "pointer",
          position: "relative",
          overflow: "hidden",
          textShadow: "0 1px 2px rgba(0,0,0,0.3)",
          boxShadow: `0 4px 15px ${color}55, inset 0 1px 0 rgba(255,255,255,0.4)`,
          transition: "all 0.2s ease"
        }),
        text: { color: "rgba(255,255,255,0.95)", fontFamily: "'Segoe UI', 'Lucida Grande', 'Trebuchet MS', sans-serif" },
        textDim: { color: "rgba(255,255,255,0.6)" }
      };
      function Bubbles() {
        const bubbles = useMemo(() => Array.from({ length: 18 }, (_, i) => ({
          id: i,
          size: 8 + Math.random() * 60,
          left: Math.random() * 100,
          delay: Math.random() * 15,
          duration: 12 + Math.random() * 18,
          opacity: 0.04 + Math.random() * 0.1
        })), []);
        return /* @__PURE__ */ React.createElement("div", { style: { position: "fixed", inset: 0, pointerEvents: "none", overflow: "hidden", zIndex: 0 } }, bubbles.map((b) => /* @__PURE__ */ React.createElement("div", { key: b.id, style: {
          position: "absolute",
          borderRadius: "50%",
          width: b.size,
          height: b.size,
          left: `${b.left}%`,
          bottom: -b.size,
          background: `radial-gradient(circle at 30% 30%, rgba(255,255,255,${b.opacity + 0.15}), rgba(255,255,255,${b.opacity * 0.5}) 40%, rgba(100,220,255,${b.opacity}) 100%)`,
          border: `1px solid rgba(255,255,255,${b.opacity + 0.05})`,
          animation: `floatUp ${b.duration}s ${b.delay}s infinite ease-in`,
          boxShadow: `inset -2px -2px ${b.size * 0.3}px rgba(0,150,200,0.05)`
        } })));
      }
      function HazyOrbs() {
        return /* @__PURE__ */ React.createElement("div", { style: { position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0 } }, /* @__PURE__ */ React.createElement("div", { style: { position: "absolute", width: 400, height: 400, top: "-10%", right: "-5%", borderRadius: "50%", background: "radial-gradient(circle, rgba(45,171,181,0.25) 0%, transparent 70%)", filter: "blur(60px)", animation: "orbDrift 20s infinite ease-in-out" } }), /* @__PURE__ */ React.createElement("div", { style: { position: "absolute", width: 300, height: 300, bottom: "10%", left: "-8%", borderRadius: "50%", background: "radial-gradient(circle, rgba(114,214,201,0.2) 0%, transparent 70%)", filter: "blur(50px)", animation: "orbDrift 25s 5s infinite ease-in-out reverse" } }), /* @__PURE__ */ React.createElement("div", { style: { position: "absolute", width: 250, height: 250, top: "40%", right: "20%", borderRadius: "50%", background: "radial-gradient(circle, rgba(168,237,234,0.15) 0%, transparent 70%)", filter: "blur(45px)", animation: "orbDrift 18s 8s infinite ease-in-out" } }));
      }
      function NavBar({ screen, setScreen }) {
        const items = [
          { id: "dashboard", icon: "\u{1F3E0}", label: "Home" },
          { id: "workout", icon: "\u26A1", label: "Workout" },
          { id: "progress", icon: "\u{1F4CA}", label: "Progress" },
          { id: "goals", icon: "\u{1F3AF}", label: "Goals" }
        ];
        return /* @__PURE__ */ React.createElement("div", { style: { position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 100, display: "flex", justifyContent: "center", padding: "0 12px 12px" } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.glass, display: "flex", gap: 4, padding: "8px 12px", maxWidth: 420, width: "100%", justifyContent: "space-around", background: "rgba(10,22,40,0.7)", backdropFilter: "blur(30px)" } }, items.map((item) => /* @__PURE__ */ React.createElement("button", { key: item.id, onClick: () => setScreen(item.id), style: {
          background: screen === item.id ? "rgba(45,171,181,0.3)" : "transparent",
          border: screen === item.id ? "1px solid rgba(45,171,181,0.4)" : "1px solid transparent",
          borderRadius: 12,
          padding: "8px 16px",
          cursor: "pointer",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 2,
          transition: "all 0.2s"
        } }, /* @__PURE__ */ React.createElement("span", { style: { fontSize: 20 } }, item.icon), /* @__PURE__ */ React.createElement("span", { style: { fontSize: 10, fontWeight: 600, color: screen === item.id ? "#72d6c9" : "rgba(255,255,255,0.5)", fontFamily: S.text.fontFamily } }, item.label)))));
      }
      function UserSelect({ onSelect }) {
        return /* @__PURE__ */ React.createElement("div", { style: { minHeight: "100vh", minHeight: "100dvh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: 24, position: "relative", zIndex: 1 } }, /* @__PURE__ */ React.createElement("div", { style: { textAlign: "center", marginBottom: 40 } }, /* @__PURE__ */ React.createElement("h1", { style: { ...S.text, fontSize: 42, fontWeight: 800, margin: 0, letterSpacing: -1, textShadow: "0 2px 20px rgba(45,171,181,0.5)" } }, "FitFam"), /* @__PURE__ */ React.createElement("p", { style: { ...S.textDim, fontSize: 15, marginTop: 8, letterSpacing: 2, textTransform: "uppercase" } }, "Family Fitness Hub")), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 20, flexWrap: "wrap", justifyContent: "center" } }, [
          { id: "alex", name: "Alex", emoji: "\u{1F9D4}", sub: "Calisthenics \xB7 Kettlebell \xB7 ROM", color: "#2dabb5" },
          { id: "sam", name: "Sam", emoji: "\u{1F3C3}", sub: "Gym \xB7 ROM", color: "#5b8def" }
        ].map((u) => /* @__PURE__ */ React.createElement("button", { key: u.id, onClick: () => onSelect(u.id), style: {
          ...S.glass,
          padding: "32px 36px",
          cursor: "pointer",
          textAlign: "center",
          minWidth: 180,
          transition: "all 0.3s ease",
          boxShadow: "0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.2)"
        } }, /* @__PURE__ */ React.createElement("div", { style: { fontSize: 56, marginBottom: 12 } }, u.emoji), /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 22, fontWeight: 700 } }, u.name), /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 11, marginTop: 8, lineHeight: 1.4 } }, u.sub)))));
      }
      function Dashboard({ data, userId, setScreen, onQuickStart }) {
        const user = data.users[userId];
        const today = (/* @__PURE__ */ new Date()).toDateString();
        const workedOutToday = user.history.some((h) => new Date(h.date).toDateString() === today);
        const last7 = user.history.filter((h) => Date.now() - new Date(h.date).getTime() < 7 * 864e5);
        const topGoal = user.goals.find((g) => g.current < g.target);
        return /* @__PURE__ */ React.createElement("div", { style: { padding: "24px 16px 100px", position: "relative", zIndex: 1, maxWidth: 480, margin: "0 auto" } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 } }, /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("h2", { style: { ...S.text, fontSize: 26, fontWeight: 800, margin: 0 } }, "Hey ", user.name, " ", user.emoji), /* @__PURE__ */ React.createElement("p", { style: { ...S.textDim, fontSize: 13, margin: "4px 0 0" } }, workedOutToday ? "Great work today! \u{1F525}" : "Ready to train?"))), /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10, marginBottom: 20 } }, [
          { label: "Streak", value: `${user.streak}\u{1F525}`, sub: "days" },
          { label: "This Week", value: last7.length, sub: "workouts" },
          { label: "Total", value: user.totalWorkouts, sub: "all time" }
        ].map((s, i) => /* @__PURE__ */ React.createElement("div", { key: i, style: { ...S.glassLight, padding: "16px 12px", textAlign: "center" } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 24, fontWeight: 800 } }, s.value), /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 10, marginTop: 4, textTransform: "uppercase", letterSpacing: 1 } }, s.label)))), /* @__PURE__ */ React.createElement("button", { onClick: onQuickStart, style: {
          ...S.glossyBtn("#2dabb5"),
          width: "100%",
          padding: "18px",
          fontSize: 17,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 10,
          marginBottom: 16
        } }, /* @__PURE__ */ React.createElement("span", { style: { fontSize: 22 } }, "\u26A1"), "Quick Start (", user.prefs.duration, "min ", user.prefs.level, ")"), /* @__PURE__ */ React.createElement("button", { onClick: () => setScreen("workout"), style: {
          ...S.glass,
          width: "100%",
          padding: "14px",
          fontSize: 14,
          cursor: "pointer",
          color: "rgba(255,255,255,0.8)",
          fontWeight: 600,
          fontFamily: S.text.fontFamily,
          textAlign: "center"
        } }, "\u{1F39B}\uFE0F Customize Workout"), topGoal && /* @__PURE__ */ React.createElement("div", { style: { ...S.glassLight, padding: 18, marginTop: 20 } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 } }, /* @__PURE__ */ React.createElement("span", { style: { ...S.text, fontSize: 14, fontWeight: 700 } }, "\u{1F3AF} Active Goal"), /* @__PURE__ */ React.createElement("span", { style: { ...S.textDim, fontSize: 12 } }, Math.round(topGoal.current / topGoal.target * 100), "%")), /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 13, marginBottom: 10 } }, topGoal.text), /* @__PURE__ */ React.createElement("div", { style: { height: 8, background: "rgba(0,0,0,0.3)", borderRadius: 4, overflow: "hidden" } }, /* @__PURE__ */ React.createElement("div", { style: { height: "100%", width: `${Math.min(100, topGoal.current / topGoal.target * 100)}%`, background: "linear-gradient(90deg, #2dabb5, #72d6c9)", borderRadius: 4, transition: "width 0.5s" } }))), user.history.length > 0 && /* @__PURE__ */ React.createElement("div", { style: { marginTop: 20 } }, /* @__PURE__ */ React.createElement("h3", { style: { ...S.text, fontSize: 15, fontWeight: 700, marginBottom: 12 } }, "Recent Workouts"), user.history.slice(-3).reverse().map((h, i) => /* @__PURE__ */ React.createElement("div", { key: i, style: { ...S.glassLight, padding: "12px 16px", marginBottom: 8, display: "flex", justifyContent: "space-between", alignItems: "center", borderRadius: 12 } }, /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 13, fontWeight: 600 } }, h.typeName), /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 11 } }, new Date(h.date).toLocaleDateString(), " \xB7 ", h.duration, "min \xB7 ", h.level)), /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 13, fontWeight: 700 } }, "\u2713 ", h.exerciseCount, " exercises")))));
      }
      function WorkoutSetup({ userId, data, onStart, onSavePrefs }) {
        var _a, _b, _c;
        const user = data.users[userId];
        const [type, setType] = useState(user.prefs.type);
        const [level, setLevel] = useState(user.prefs.level);
        const [duration, setDuration] = useState(user.prefs.duration);
        const types = WORKOUT_TYPES[userId] || [];
        const handleSave = () => {
          onSavePrefs(userId, { type, level, duration });
        };
        const handleStart = () => {
          handleSave();
          onStart(type, level, duration);
        };
        const levelColors = { easy: "#4ade80", mid: "#facc15", challenging: "#f87171" };
        const levelLabels = { easy: "Easy", mid: "Moderate", challenging: "Challenging" };
        return /* @__PURE__ */ React.createElement("div", { style: { padding: "24px 16px 100px", position: "relative", zIndex: 1, maxWidth: 480, margin: "0 auto" } }, /* @__PURE__ */ React.createElement("h2", { style: { ...S.text, fontSize: 24, fontWeight: 800, marginBottom: 4 } }, "Build Your Workout"), /* @__PURE__ */ React.createElement("p", { style: { ...S.textDim, fontSize: 13, marginBottom: 24 } }, "Configure and go, or save as your Quick Start default."), /* @__PURE__ */ React.createElement("div", { style: { marginBottom: 20 } }, /* @__PURE__ */ React.createElement("label", { style: { ...S.text, fontSize: 13, fontWeight: 700, display: "block", marginBottom: 10, textTransform: "uppercase", letterSpacing: 1 } }, "Workout Type"), /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 } }, types.map((t) => /* @__PURE__ */ React.createElement("button", { key: t.id, onClick: () => setType(t.id), style: {
          ...S.glassLight,
          padding: "14px 12px",
          cursor: "pointer",
          textAlign: "center",
          border: type === t.id ? "2px solid #2dabb5" : "1px solid rgba(255,255,255,0.15)",
          borderRadius: 14,
          transition: "all 0.2s",
          background: type === t.id ? "rgba(45,171,181,0.2)" : "rgba(255,255,255,0.08)"
        } }, /* @__PURE__ */ React.createElement("div", { style: { fontSize: 24 } }, t.emoji), /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 12, fontWeight: 600, marginTop: 4 } }, t.label))))), /* @__PURE__ */ React.createElement("div", { style: { marginBottom: 20 } }, /* @__PURE__ */ React.createElement("label", { style: { ...S.text, fontSize: 13, fontWeight: 700, display: "block", marginBottom: 10, textTransform: "uppercase", letterSpacing: 1 } }, "Intensity"), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 8 } }, Object.entries(levelLabels).map(([key, label]) => /* @__PURE__ */ React.createElement("button", { key, onClick: () => setLevel(key), style: {
          flex: 1,
          padding: "12px 8px",
          cursor: "pointer",
          borderRadius: 14,
          textAlign: "center",
          border: level === key ? `2px solid ${levelColors[key]}` : "1px solid rgba(255,255,255,0.15)",
          background: level === key ? `${levelColors[key]}22` : "rgba(255,255,255,0.08)",
          transition: "all 0.2s"
        } }, /* @__PURE__ */ React.createElement("div", { style: { fontSize: 20 } }, key === "easy" ? "\u{1F331}" : key === "mid" ? "\u{1F525}" : "\u{1F480}"), /* @__PURE__ */ React.createElement("div", { style: { color: level === key ? levelColors[key] : "rgba(255,255,255,0.7)", fontSize: 12, fontWeight: 700, marginTop: 4, fontFamily: S.text.fontFamily } }, label))))), /* @__PURE__ */ React.createElement("div", { style: { marginBottom: 28 } }, /* @__PURE__ */ React.createElement("label", { style: { ...S.text, fontSize: 13, fontWeight: 700, display: "block", marginBottom: 10, textTransform: "uppercase", letterSpacing: 1 } }, "Duration"), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 8 } }, [15, 30, 45, 60].map((d) => /* @__PURE__ */ React.createElement("button", { key: d, onClick: () => setDuration(d), style: {
          flex: 1,
          padding: "14px 8px",
          cursor: "pointer",
          borderRadius: 14,
          textAlign: "center",
          border: duration === d ? "2px solid #2dabb5" : "1px solid rgba(255,255,255,0.15)",
          background: duration === d ? "rgba(45,171,181,0.2)" : "rgba(255,255,255,0.08)",
          transition: "all 0.2s"
        } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 18, fontWeight: 800 } }, d), /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 10 } }, "min"))))), /* @__PURE__ */ React.createElement("div", { style: { ...S.glassLight, padding: 16, marginBottom: 16, borderRadius: 14 } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 11, marginBottom: 6, textTransform: "uppercase", letterSpacing: 1 } }, "Preview: ~", ((_a = TIME_CONFIGS[duration]) == null ? void 0 : _a.exercises) || 5, " exercises"), /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 14 } }, (_b = types.find((t) => t.id === type)) == null ? void 0 : _b.emoji, " ", (_c = types.find((t) => t.id === type)) == null ? void 0 : _c.label, " \xB7 ", levelLabels[level], " \xB7 ", duration, "min")), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 10 } }, /* @__PURE__ */ React.createElement("button", { onClick: handleSave, style: { ...S.glass, flex: 1, padding: "14px", cursor: "pointer", color: "rgba(255,255,255,0.8)", fontWeight: 600, fontSize: 13, fontFamily: S.text.fontFamily, textAlign: "center" } }, "\u{1F4BE} Save Default"), /* @__PURE__ */ React.createElement("button", { onClick: handleStart, style: { ...S.glossyBtn("#2dabb5"), flex: 2, padding: "14px", fontSize: 15 } }, "\u25B6 Start Workout")));
      }
      function ActiveWorkout({ exercises, level, duration, typeName, onComplete, onCancel }) {
        const [completed, setCompleted] = useState(/* @__PURE__ */ new Set());
        const [elapsed, setElapsed] = useState(0);
        const [expanded, setExpanded] = useState(null);
        const [restTime, setRestTime] = useState(0);
        const [restDuration, setRestDuration] = useState(60);
        const restRef = useRef(null);
        const timerRef = useRef(null);
        useEffect(() => {
          timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1e3);
          return () => clearInterval(timerRef.current);
        }, []);
        const startRest = () => {
          setRestTime(restDuration);
          if (restRef.current) clearInterval(restRef.current);
          restRef.current = setInterval(() => {
            setRestTime((t) => {
              if (t <= 1) {
                clearInterval(restRef.current);
                restRef.current = null;
                return 0;
              }
              return t - 1;
            });
          }, 1e3);
        };
        const skipRest = () => {
          setRestTime(0);
          if (restRef.current) {
            clearInterval(restRef.current);
            restRef.current = null;
          }
        };
        useEffect(() => () => {
          if (restRef.current) clearInterval(restRef.current);
        }, []);
        const toggle = (id) => {
          setCompleted((prev) => {
            const next = new Set(prev);
            if (next.has(id)) {
              next.delete(id);
            } else {
              next.add(id);
              startRest();
            }
            return next;
          });
        };
        const allDone = completed.size === exercises.length;
        const pct = exercises.length > 0 ? completed.size / exercises.length * 100 : 0;
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        return /* @__PURE__ */ React.createElement("div", { style: { padding: "24px 16px 100px", position: "relative", zIndex: 1, maxWidth: 480, margin: "0 auto" } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 } }, /* @__PURE__ */ React.createElement("button", { onClick: onCancel, style: { background: "none", border: "none", color: "rgba(255,255,255,0.6)", fontSize: 14, cursor: "pointer", fontFamily: S.text.fontFamily } }, "\u2190 Back"), /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 20, fontWeight: 800 } }, String(mins).padStart(2, "0"), ":", String(secs).padStart(2, "0"))), /* @__PURE__ */ React.createElement("h2", { style: { ...S.text, fontSize: 20, fontWeight: 800, marginBottom: 4 } }, typeName), /* @__PURE__ */ React.createElement("p", { style: { ...S.textDim, fontSize: 12, marginBottom: 16 } }, completed.size, "/", exercises.length, " complete \xB7 ", level), /* @__PURE__ */ React.createElement("div", { style: { height: 6, background: "rgba(0,0,0,0.3)", borderRadius: 3, marginBottom: 12, overflow: "hidden" } }, /* @__PURE__ */ React.createElement("div", { style: { height: "100%", width: `${pct}%`, background: "linear-gradient(90deg, #2dabb5, #72d6c9)", borderRadius: 3, transition: "width 0.3s" } })), restTime > 0 && /* @__PURE__ */ React.createElement("div", { style: { ...S.glassLight, padding: 16, marginBottom: 16, textAlign: "center", borderRadius: 14, border: "1px solid rgba(45,171,181,0.4)" } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 11, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 } }, "Rest Timer"), /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 32, fontWeight: 800 } }, String(Math.floor(restTime / 60)).padStart(2, "0"), ":", String(restTime % 60).padStart(2, "0")), /* @__PURE__ */ React.createElement("button", { onClick: skipRest, style: { ...S.glossyBtn("#2dabb5"), padding: "6px 18px", fontSize: 12, marginTop: 8 } }, "Skip Rest")), restTime === 0 && completed.size > 0 && completed.size < exercises.length && /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 6, marginBottom: 12, justifyContent: "center" } }, /* @__PURE__ */ React.createElement("span", { style: { ...S.textDim, fontSize: 11 } }, "Rest:"), [30, 60, 90, 120].map((s) => /* @__PURE__ */ React.createElement("button", { key: s, onClick: () => {
          setRestDuration(s);
          setRestTime(s);
          startRest();
        }, style: {
          background: restDuration === s ? "rgba(45,171,181,0.3)" : "rgba(255,255,255,0.08)",
          border: "1px solid rgba(255,255,255,0.15)",
          borderRadius: 8,
          padding: "3px 10px",
          cursor: "pointer",
          color: "rgba(255,255,255,0.7)",
          fontSize: 11,
          fontWeight: 600,
          fontFamily: S.text.fontFamily
        } }, s, "s"))), exercises.map((ex) => {
          const done = completed.has(ex.id);
          const isExpanded = expanded === ex.id;
          return /* @__PURE__ */ React.createElement("div", { key: ex.id, style: {
            ...S.glassLight,
            marginBottom: 10,
            borderRadius: 14,
            overflow: "hidden",
            border: done ? "1px solid rgba(74,222,128,0.4)" : "1px solid rgba(255,255,255,0.15)",
            background: done ? "rgba(74,222,128,0.1)" : "rgba(255,255,255,0.08)",
            transition: "all 0.3s"
          } }, /* @__PURE__ */ React.createElement("div", { style: { padding: "14px 16px", display: "flex", alignItems: "center", gap: 12, cursor: "pointer" }, onClick: () => setExpanded(isExpanded ? null : ex.id) }, /* @__PURE__ */ React.createElement("button", { onClick: (e) => {
            e.stopPropagation();
            toggle(ex.id);
          }, style: {
            width: 28,
            height: 28,
            borderRadius: 8,
            border: done ? "2px solid #4ade80" : "2px solid rgba(255,255,255,0.3)",
            background: done ? "#4ade80" : "transparent",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "white",
            fontSize: 14,
            fontWeight: 800,
            flexShrink: 0,
            transition: "all 0.2s"
          } }, done ? "\u2713" : ""), /* @__PURE__ */ React.createElement("div", { style: { flex: 1 } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 14, fontWeight: 700, textDecoration: done ? "line-through" : "none", opacity: done ? 0.6 : 1 } }, ex.name), /* @__PURE__ */ React.createElement("div", { style: { color: "#72d6c9", fontSize: 12, fontWeight: 600, marginTop: 2, fontFamily: S.text.fontFamily } }, ex.prescription)), /* @__PURE__ */ React.createElement("span", { style: { ...S.textDim, fontSize: 12, transform: isExpanded ? "rotate(180deg)" : "", transition: "transform 0.2s" } }, "\u25BC")), isExpanded && /* @__PURE__ */ React.createElement("div", { style: { padding: "0 16px 14px 56px" } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 12, lineHeight: 1.6 } }, ex.desc), /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 11, marginTop: 6 } }, "Target: ", ex.muscle, " \xB7 ~", ex.time, "min")));
        }), allDone && /* @__PURE__ */ React.createElement("button", { onClick: () => onComplete(elapsed), style: { ...S.glossyBtn("#4ade80"), width: "100%", padding: "16px", fontSize: 16, marginTop: 16 } }, "\u{1F389} Complete Workout!"), !allDone && completed.size > 0 && /* @__PURE__ */ React.createElement("button", { onClick: () => onComplete(elapsed), style: {
          ...S.glass,
          width: "100%",
          padding: "14px",
          fontSize: 13,
          marginTop: 12,
          cursor: "pointer",
          color: "rgba(255,255,255,0.7)",
          fontWeight: 600,
          fontFamily: S.text.fontFamily,
          textAlign: "center"
        } }, "Finish Early (", completed.size, "/", exercises.length, " done)"));
      }
      function ProgressScreen({ data, userId, onDeleteHistory }) {
        const user = data.users[userId];
        const last30 = [];
        for (let i = 29; i >= 0; i--) {
          const d = /* @__PURE__ */ new Date();
          d.setDate(d.getDate() - i);
          const ds = d.toDateString();
          const count = user.history.filter((h) => new Date(h.date).toDateString() === ds).length;
          last30.push({ date: d, count, day: d.getDate(), dow: ["S", "M", "T", "W", "T", "F", "S"][d.getDay()] });
        }
        const totalTime = user.history.reduce((acc, h) => acc + (h.duration || 0), 0);
        const totalExercises = user.history.reduce((acc, h) => acc + (h.exerciseCount || 0), 0);
        const byType = {};
        user.history.forEach((h) => {
          byType[h.typeName] = (byType[h.typeName] || 0) + 1;
        });
        const topType = Object.entries(byType).sort((a, b) => b[1] - a[1])[0];
        return /* @__PURE__ */ React.createElement("div", { style: { padding: "24px 16px 100px", position: "relative", zIndex: 1, maxWidth: 480, margin: "0 auto" } }, /* @__PURE__ */ React.createElement("h2", { style: { ...S.text, fontSize: 24, fontWeight: 800, marginBottom: 4 } }, "Progress"), /* @__PURE__ */ React.createElement("p", { style: { ...S.textDim, fontSize: 13, marginBottom: 20 } }, "Your training journey at a glance."), /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 20 } }, [
          { label: "Total Workouts", value: user.totalWorkouts },
          { label: "Total Minutes", value: totalTime },
          { label: "Exercises Done", value: totalExercises },
          { label: "Current Streak", value: `${user.streak} \u{1F525}` }
        ].map((s, i) => /* @__PURE__ */ React.createElement("div", { key: i, style: { ...S.glassLight, padding: 16, textAlign: "center" } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 22, fontWeight: 800 } }, s.value), /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 10, marginTop: 4, textTransform: "uppercase", letterSpacing: 1 } }, s.label)))), /* @__PURE__ */ React.createElement("div", { style: { ...S.glassLight, padding: 16, marginBottom: 20 } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 14, fontWeight: 700, marginBottom: 12 } }, "Last 30 Days"), /* @__PURE__ */ React.createElement("div", { style: { display: "grid", gridTemplateColumns: "repeat(10, 1fr)", gap: 4 } }, last30.map((d, i) => /* @__PURE__ */ React.createElement("div", { key: i, style: {
          width: "100%",
          aspectRatio: "1",
          borderRadius: 6,
          background: d.count > 0 ? `rgba(45,171,181,${Math.min(0.3 + d.count * 0.3, 1)})` : "rgba(255,255,255,0.06)",
          border: d.count > 0 ? "1px solid rgba(45,171,181,0.5)" : "1px solid rgba(255,255,255,0.08)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 9,
          color: d.count > 0 ? "#72d6c9" : "rgba(255,255,255,0.3)",
          fontWeight: 700,
          fontFamily: S.text.fontFamily
        } }, d.day)))), topType && /* @__PURE__ */ React.createElement("div", { style: { ...S.glassLight, padding: 16 } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 14, fontWeight: 700, marginBottom: 6 } }, "Most Trained"), /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 18, fontWeight: 800 } }, topType[0]), /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 12 } }, topType[1], " session", topType[1] > 1 ? "s" : "")), user.history.length > 0 && /* @__PURE__ */ React.createElement("div", { style: { ...S.glassLight, padding: 16, marginTop: 20 } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 14, fontWeight: 700, marginBottom: 12 } }, "Workout History"), user.history.slice().reverse().map((h, ri) => {
          const idx = user.history.length - 1 - ri;
          return /* @__PURE__ */ React.createElement("div", { key: idx, style: { display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 0", borderBottom: "1px solid rgba(255,255,255,0.08)" } }, /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 13, fontWeight: 600 } }, h.typeName || "Workout"), /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 11 } }, new Date(h.date).toLocaleDateString(), " \xB7 ", h.duration || 0, "min \xB7 ", h.exerciseCount || 0, " exercises")), /* @__PURE__ */ React.createElement("button", { onClick: () => {
            if (confirm("Remove this entry?")) onDeleteHistory(userId, idx);
          }, style: {
            background: "rgba(239,68,68,0.15)",
            border: "1px solid rgba(239,68,68,0.3)",
            borderRadius: 8,
            padding: "4px 8px",
            cursor: "pointer",
            color: "#ef4444",
            fontSize: 10,
            fontWeight: 700,
            fontFamily: S.text.fontFamily,
            flexShrink: 0
          } }, "\u2715"));
        })), user.history.length === 0 && /* @__PURE__ */ React.createElement("div", { style: { ...S.glassLight, padding: 32, textAlign: "center" } }, /* @__PURE__ */ React.createElement("div", { style: { fontSize: 40, marginBottom: 12 } }, "\u{1F31F}"), /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 15, fontWeight: 600 } }, "No workouts yet"), /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 13, marginTop: 6 } }, "Complete your first workout to start tracking!")));
      }
      function GoalsScreen({ data, userId, onUpdateGoal, onAddGoal, onDeleteGoal }) {
        const user = data.users[userId];
        const [adding, setAdding] = useState(false);
        const [newText, setNewText] = useState("");
        const [newTarget, setNewTarget] = useState("");
        const [newUnit, setNewUnit] = useState("reps");
        const handleAdd = () => {
          if (!newText || !newTarget) return;
          onAddGoal(userId, { id: `custom-${Date.now()}`, text: newText, target: Number(newTarget), unit: newUnit, current: 0 });
          setNewText("");
          setNewTarget("");
          setAdding(false);
        };
        return /* @__PURE__ */ React.createElement("div", { style: { padding: "24px 16px 100px", position: "relative", zIndex: 1, maxWidth: 480, margin: "0 auto" } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 } }, /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("h2", { style: { ...S.text, fontSize: 24, fontWeight: 800, margin: 0 } }, "Goals"), /* @__PURE__ */ React.createElement("p", { style: { ...S.textDim, fontSize: 13, margin: "4px 0 0" } }, "Track milestones and achievements")), /* @__PURE__ */ React.createElement("button", { onClick: () => setAdding(!adding), style: { ...S.glossyBtn("#2dabb5"), padding: "10px 16px", fontSize: 13 } }, "+ Add Goal")), adding && /* @__PURE__ */ React.createElement("div", { style: { ...S.glassLight, padding: 16, marginBottom: 16 } }, /* @__PURE__ */ React.createElement("input", { value: newText, onChange: (e) => setNewText(e.target.value), placeholder: "Goal description...", style: {
          width: "100%",
          padding: "10px 12px",
          borderRadius: 10,
          border: "1px solid rgba(255,255,255,0.2)",
          background: "rgba(0,0,0,0.2)",
          color: "white",
          fontSize: 13,
          marginBottom: 8,
          fontFamily: S.text.fontFamily,
          boxSizing: "border-box"
        } }), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 8 } }, /* @__PURE__ */ React.createElement("input", { value: newTarget, onChange: (e) => setNewTarget(e.target.value), placeholder: "Target #", type: "number", style: {
          flex: 1,
          padding: "10px 12px",
          borderRadius: 10,
          border: "1px solid rgba(255,255,255,0.2)",
          background: "rgba(0,0,0,0.2)",
          color: "white",
          fontSize: 13,
          fontFamily: S.text.fontFamily
        } }), /* @__PURE__ */ React.createElement("select", { value: newUnit, onChange: (e) => setNewUnit(e.target.value), style: {
          flex: 1,
          padding: "10px 12px",
          borderRadius: 10,
          border: "1px solid rgba(255,255,255,0.2)",
          background: "rgba(0,0,0,0.3)",
          color: "white",
          fontSize: 13,
          fontFamily: S.text.fontFamily
        } }, ["reps", "seconds", "minutes", "days", "sessions", "% ROM", "lbs"].map((u) => /* @__PURE__ */ React.createElement("option", { key: u, value: u }, u)))), /* @__PURE__ */ React.createElement("button", { onClick: handleAdd, style: { ...S.glossyBtn("#4ade80"), width: "100%", padding: "10px", fontSize: 13, marginTop: 10 } }, "Save Goal")), user.goals.map((goal) => {
          const pct = Math.min(100, Math.round(goal.current / goal.target * 100));
          const done = pct >= 100;
          return /* @__PURE__ */ React.createElement("div", { key: goal.id, style: {
            ...S.glassLight,
            padding: 16,
            marginBottom: 12,
            borderRadius: 14,
            border: done ? "1px solid rgba(74,222,128,0.4)" : "1px solid rgba(255,255,255,0.15)"
          } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 14, fontWeight: 700 } }, done ? "\u{1F3C6} " : "\u{1F3AF} ", goal.text), /* @__PURE__ */ React.createElement("span", { style: { color: done ? "#4ade80" : "#72d6c9", fontSize: 13, fontWeight: 800, fontFamily: S.text.fontFamily } }, pct, "%")), /* @__PURE__ */ React.createElement("div", { style: { height: 8, background: "rgba(0,0,0,0.3)", borderRadius: 4, overflow: "hidden", marginBottom: 10 } }, /* @__PURE__ */ React.createElement("div", { style: {
            height: "100%",
            width: `${pct}%`,
            borderRadius: 4,
            transition: "width 0.5s",
            background: done ? "linear-gradient(90deg, #4ade80, #22c55e)" : "linear-gradient(90deg, #2dabb5, #72d6c9)"
          } })), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between" } }, /* @__PURE__ */ React.createElement("span", { style: { ...S.textDim, fontSize: 12 } }, goal.current, " / ", goal.target, " ", goal.unit), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 6, alignItems: "center" } }, !done && [1, 5, 10].map((inc) => /* @__PURE__ */ React.createElement("button", { key: inc, onClick: () => onUpdateGoal(userId, goal.id, Math.min(goal.target, goal.current + inc)), style: {
            background: "rgba(45,171,181,0.2)",
            border: "1px solid rgba(45,171,181,0.3)",
            borderRadius: 8,
            padding: "4px 10px",
            cursor: "pointer",
            color: "#72d6c9",
            fontSize: 11,
            fontWeight: 700,
            fontFamily: S.text.fontFamily
          } }, "+", inc)), /* @__PURE__ */ React.createElement("button", { onClick: () => {
            if (confirm("Delete this goal?")) onDeleteGoal(userId, goal.id);
          }, style: {
            background: "rgba(239,68,68,0.15)",
            border: "1px solid rgba(239,68,68,0.3)",
            borderRadius: 8,
            padding: "4px 10px",
            cursor: "pointer",
            color: "#ef4444",
            fontSize: 11,
            fontWeight: 700,
            fontFamily: S.text.fontFamily
          } }, "\u2715"))));
        }));
      }
      function CompletionScreen({ onDone, duration, exerciseCount, typeName }) {
        return /* @__PURE__ */ React.createElement("div", { style: { minHeight: "100vh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: 24, position: "relative", zIndex: 1, textAlign: "center" } }, /* @__PURE__ */ React.createElement("div", { style: { fontSize: 80, marginBottom: 16, animation: "bounceIn 0.6s ease" } }, "\u{1F389}"), /* @__PURE__ */ React.createElement("h1", { style: { ...S.text, fontSize: 32, fontWeight: 800, marginBottom: 8 } }, "Workout Complete!"), /* @__PURE__ */ React.createElement("p", { style: { ...S.textDim, fontSize: 15, marginBottom: 32 } }, "Great work crushing that ", typeName, " session."), /* @__PURE__ */ React.createElement("div", { style: { display: "flex", gap: 16, marginBottom: 32 } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.glassLight, padding: "16px 24px", textAlign: "center" } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 28, fontWeight: 800 } }, Math.floor(duration / 60), /* @__PURE__ */ React.createElement("span", { style: { fontSize: 14 } }, "m"), " ", duration % 60, /* @__PURE__ */ React.createElement("span", { style: { fontSize: 14 } }, "s")), /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 11, marginTop: 4 } }, "Duration")), /* @__PURE__ */ React.createElement("div", { style: { ...S.glassLight, padding: "16px 24px", textAlign: "center" } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 28, fontWeight: 800 } }, exerciseCount), /* @__PURE__ */ React.createElement("div", { style: { ...S.textDim, fontSize: 11, marginTop: 4 } }, "Exercises"))), /* @__PURE__ */ React.createElement("button", { onClick: onDone, style: { ...S.glossyBtn("#2dabb5"), padding: "16px 48px", fontSize: 16 } }, "Back to Dashboard"));
      }
      function FitFamApp() {
        const [data, setData] = useState(null);
        const [userId, setUserId] = useState(null);
        const [screen, setScreen] = useState("dashboard");
        const [activeWorkout, setActiveWorkout] = useState(null);
        const [completion, setCompletion] = useState(null);
        const [loaded, setLoaded] = useState(false);
        useEffect(() => {
          (async () => {
            const saved = await loadData();
            if (saved && saved.users) {
              const defaults = getDefaultData();
              const merged = { ...defaults, ...saved };
              Object.keys(defaults.users).forEach((uid) => {
                if (!merged.users[uid]) merged.users[uid] = defaults.users[uid];
                else merged.users[uid] = { ...defaults.users[uid], ...merged.users[uid] };
              });
              setData(merged);
            } else {
              setData(getDefaultData());
            }
            setLoaded(true);
          })();
        }, []);
        useEffect(() => {
          if (data && loaded) saveData(data);
        }, [data, loaded]);
        const updateData = useCallback((fn) => {
          setData((prev) => {
            const next = JSON.parse(JSON.stringify(prev));
            fn(next);
            return next;
          });
        }, []);
        const handleSavePrefs = useCallback((uid, prefs) => {
          updateData((d) => {
            d.users[uid].prefs = prefs;
          });
        }, [updateData]);
        const handleStartWorkout = useCallback((typeId, level, duration) => {
          const types = WORKOUT_TYPES[userId] || [];
          const type = types.find((t) => t.id === typeId);
          const exercises = generateWorkout(userId, typeId, level, duration);
          setActiveWorkout({ exercises, level, duration, typeId, typeName: (type == null ? void 0 : type.label) || typeId });
          setScreen("active");
        }, [userId]);
        const handleQuickStart = useCallback(() => {
          const user = data.users[userId];
          handleStartWorkout(user.prefs.type, user.prefs.level, user.prefs.duration);
        }, [data, userId, handleStartWorkout]);
        const handleCompleteWorkout = useCallback((elapsed) => {
          const workout = activeWorkout;
          updateData((d) => {
            const u = d.users[userId];
            u.history.push({
              date: (/* @__PURE__ */ new Date()).toISOString(),
              typeName: workout.typeName,
              level: workout.level,
              duration: workout.duration,
              exerciseCount: workout.exercises.length,
              elapsed
            });
            u.totalWorkouts++;
            const dates = u.history.map((h) => new Date(h.date).toDateString());
            const uniqueDates = [...new Set(dates)].sort((a, b) => new Date(b) - new Date(a));
            let streak = 0;
            for (let i = 0; i < uniqueDates.length; i++) {
              const expected = /* @__PURE__ */ new Date();
              expected.setDate(expected.getDate() - i);
              if (uniqueDates[i] === expected.toDateString()) streak++;
              else break;
            }
            u.streak = streak;
          });
          setCompletion({ duration: elapsed, exerciseCount: workout.exercises.length, typeName: workout.typeName });
          setActiveWorkout(null);
          setScreen("complete");
        }, [activeWorkout, userId, updateData]);
        const handleUpdateGoal = useCallback((uid, goalId, newVal) => {
          updateData((d) => {
            const goal = d.users[uid].goals.find((g) => g.id === goalId);
            if (goal) goal.current = newVal;
          });
        }, [updateData]);
        const handleAddGoal = useCallback((uid, goal) => {
          updateData((d) => {
            d.users[uid].goals.push(goal);
          });
        }, [updateData]);
        const handleDeleteGoal = useCallback((uid, goalId) => {
          updateData((d) => {
            d.users[uid].goals = d.users[uid].goals.filter((g) => g.id !== goalId);
          });
        }, [updateData]);
        const handleDeleteHistory = useCallback((uid, idx) => {
          updateData((d) => {
            d.users[uid].history.splice(idx, 1);
          });
        }, [updateData]);
        if (!loaded || !data) {
          return /* @__PURE__ */ React.createElement("div", { style: { minHeight: "100vh", background: S.bg, display: "flex", alignItems: "center", justifyContent: "center" } }, /* @__PURE__ */ React.createElement("div", { style: { ...S.text, fontSize: 24, fontWeight: 700 } }, "Loading FitFam..."));
        }
        return /* @__PURE__ */ React.createElement("div", { style: { minHeight: "100vh", minHeight: "100dvh", background: S.bg, position: "relative", overflow: "hidden" } }, /* @__PURE__ */ React.createElement(Bubbles, null), /* @__PURE__ */ React.createElement(HazyOrbs, null), !userId && /* @__PURE__ */ React.createElement(UserSelect, { onSelect: (id) => {
          setUserId(id);
          setScreen("dashboard");
        } }), userId && screen !== "active" && screen !== "complete" && /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("div", { style: { position: "sticky", top: 0, zIndex: 50, padding: "12px 16px", display: "flex", justifyContent: "space-between", alignItems: "center", background: "rgba(10,22,40,0.5)", backdropFilter: "blur(20px)" } }, /* @__PURE__ */ React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 8 } }, /* @__PURE__ */ React.createElement("span", { style: { ...S.text, fontSize: 18, fontWeight: 800 } }, "FitFam"), /* @__PURE__ */ React.createElement("span", { style: { ...S.textDim, fontSize: 12 } }, "|"), /* @__PURE__ */ React.createElement("span", { style: { ...S.text, fontSize: 14 } }, data.users[userId].emoji, " ", data.users[userId].name)), /* @__PURE__ */ React.createElement("button", { onClick: () => {
          setUserId(null);
          setScreen("dashboard");
        }, style: {
          background: "rgba(255,255,255,0.1)",
          border: "1px solid rgba(255,255,255,0.15)",
          borderRadius: 10,
          padding: "6px 14px",
          cursor: "pointer",
          color: "rgba(255,255,255,0.7)",
          fontSize: 12,
          fontWeight: 600,
          fontFamily: S.text.fontFamily
        } }, "Switch User")), screen === "dashboard" && /* @__PURE__ */ React.createElement(Dashboard, { data, userId, setScreen, onQuickStart: handleQuickStart }), screen === "workout" && /* @__PURE__ */ React.createElement(WorkoutSetup, { userId, data, onStart: handleStartWorkout, onSavePrefs: handleSavePrefs }), screen === "progress" && /* @__PURE__ */ React.createElement(ProgressScreen, { data, userId, onDeleteHistory: handleDeleteHistory }), screen === "goals" && /* @__PURE__ */ React.createElement(GoalsScreen, { data, userId, onUpdateGoal: handleUpdateGoal, onAddGoal: handleAddGoal, onDeleteGoal: handleDeleteGoal }), /* @__PURE__ */ React.createElement(NavBar, { screen, setScreen })), userId && screen === "active" && activeWorkout && /* @__PURE__ */ React.createElement(ActiveWorkout, { exercises: activeWorkout.exercises, level: activeWorkout.level, duration: activeWorkout.duration, typeName: activeWorkout.typeName, onComplete: handleCompleteWorkout, onCancel: () => {
          setActiveWorkout(null);
          setScreen("dashboard");
        } }), userId && screen === "complete" && completion && /* @__PURE__ */ React.createElement(CompletionScreen, { ...completion, onDone: () => {
          setCompletion(null);
          setScreen("dashboard");
        } }));
      }
      ReactDOM.createRoot(document.getElementById("root")).render(/* @__PURE__ */ React.createElement(FitFamApp, null));
    }
  });
  require_fitfam();
})();
