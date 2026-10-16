/**
 * FitFam Exercise Science Module
 *
 * Evidence-based exercise database, programming logic, and analytics
 * helpers. Built around principles from Schoenfeld, Helms, Israetel, and
 * the NSCA Essentials of Strength Training & Conditioning.
 *
 * Key concepts:
 *   - Compound vs isolation lifts
 *   - RIR (reps in reserve) / RPE (rate of perceived exertion) for autoregulation
 *   - Volume landmarks (MEV, MAV, MRV) per muscle group
 *   - Estimated 1-rep max via Epley formula
 *   - Progressive overload tracking
 */

export type Equipment =
  | "barbell"
  | "dumbbell"
  | "kettlebell"
  | "bodyweight"
  | "cable"
  | "machine"
  | "band"
  | "trap-bar";

export type MuscleGroup =
  | "chest"
  | "back"
  | "shoulders"
  | "biceps"
  | "triceps"
  | "quads"
  | "hamstrings"
  | "glutes"
  | "calves"
  | "core"
  | "forearms";

export type Pattern =
  | "horizontal-push"
  | "horizontal-pull"
  | "vertical-push"
  | "vertical-pull"
  | "squat"
  | "hinge"
  | "lunge"
  | "carry"
  | "rotation"
  | "isolation";

export interface Exercise {
  id: string;
  name: string;
  equipment: Equipment;
  primary: MuscleGroup[];
  secondary: MuscleGroup[];
  pattern: Pattern;
  isCompound: boolean;
  /** 1 = master/staple, 2 = great, 3 = situational */
  tier: 1 | 2 | 3;
  formCues: string[];
  /** Common substitutes if equipment is unavailable */
  substitutes?: string[];
  /** Brief safety notes */
  safety?: string;
  /** Inline SVG stick figure (viewBox 0 0 150 190, currentColor strokes). Hand-authored. */
  figure?: string;
}

/**
 * Curated list of evidence-based exercises. This is intentionally
 * focused on the highest-ROI movements rather than exhaustive: quality
 * over quantity. Add more over time.
 */
export const EXERCISE_DB: Exercise[] = [
  // ── PUSH ──────────────────────────────────────────────────────────
  {
    id: "bench-press",
    name: "Barbell Bench Press",
    equipment: "barbell",
    primary: ["chest"],
    secondary: ["triceps", "shoulders"],
    pattern: "horizontal-push",
    isCompound: true,
    tier: 1,
    formCues: [
      "Set your shoulder blades down and back, like tucking them into your back pockets, before you lift",
      "Bring the bar to the bottom of your chest, not up by your collarbone",
      "Push your feet into the floor to stay solid",
      "Keep your wrists straight, stacked right over your elbows",
    ],
    substitutes: ["dumbbell-bench", "pushup", "machine-chest-press"],
    safety: "Always have someone spotting you, or use safety bars, when the weight is heavy.",
  },
  {
    id: "dumbbell-bench",
    name: "Dumbbell Bench Press",
    equipment: "dumbbell",
    primary: ["chest"],
    secondary: ["triceps", "shoulders"],
    pattern: "horizontal-push",
    isCompound: true,
    tier: 1,
    formCues: [
      "Lower the dumbbells to chest level, nice and controlled",
      "Keep your elbows about halfway out from your sides, not flared wide",
      "Press up and bring the dumbbells slightly together at the top",
    ],
    substitutes: ["bench-press", "pushup"],
  },
  {
    id: "pushup",
    name: "Push-Up",
    equipment: "bodyweight",
    primary: ["chest"],
    secondary: ["triceps", "shoulders", "core"],
    pattern: "horizontal-push",
    isCompound: true,
    tier: 1,
    formCues: [
      "Keep your body in one straight line from head to heels",
      "Lower until your chest almost touches the floor",
      "Keep your elbows about halfway out from your sides, not flared wide",
    ],
    substitutes: ["dumbbell-bench", "dip"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Push-Up</title><line x1="14" y1="154" x2="142" y2="154" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="40" cy="92" r="11"/><line x1="50" y1="97" x2="128" y2="138"/><line x1="60" y1="101" x2="56" y2="152"/><line x1="128" y1="138" x2="124" y2="152"/></g></svg>`,
  },
  {
    id: "ohp",
    name: "Overhead Press",
    equipment: "barbell",
    primary: ["shoulders"],
    secondary: ["triceps", "core"],
    pattern: "vertical-push",
    isCompound: true,
    tier: 1,
    formCues: [
      "Tighten your stomach before you lift the bar off",
      "Squeeze your glutes so your lower back doesn't arch",
      "Press the bar straight up until it's over your ears",
    ],
    substitutes: ["dumbbell-shoulder-press", "pike-pushup"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Strict Overhead Press</title><line x1="46" y1="24" x2="104" y2="24" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="75" cy="52" r="11"/><line x1="75" y1="63" x2="75" y2="116"/><path d="M75 72 L60 46 L58 26"/><path d="M75 72 L90 46 L92 26"/><path d="M75 116 L64 148 L64 176"/><path d="M75 116 L86 148 L86 176"/></g></svg>`,
  },
  {
    id: "dumbbell-shoulder-press",
    name: "Dumbbell Shoulder Press",
    equipment: "dumbbell",
    primary: ["shoulders"],
    secondary: ["triceps"],
    pattern: "vertical-push",
    isCompound: true,
    tier: 1,
    formCues: [
      "Start with the dumbbells at shoulder height, palms facing forward",
      "Press up and bring them slightly together at the top",
      "Lower slowly, all the way back down",
    ],
    substitutes: ["ohp", "lateral-raise"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Single-Arm KB Press</title><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="70" cy="52" r="11"/><line x1="70" y1="63" x2="70" y2="116"/><path d="M70 72 L88 46 L90 28"/><circle cx="90" cy="23" r="6" stroke-opacity="0.55"/><path d="M70 74 L57 98 L59 116"/><path d="M70 116 L60 148 L60 176"/><path d="M70 116 L82 148 L82 176"/></g></svg>`,
  },
  {
    id: "dip",
    name: "Parallel Bar Dip",
    equipment: "bodyweight",
    primary: ["chest", "triceps"],
    secondary: ["shoulders"],
    pattern: "vertical-push",
    isCompound: true,
    tier: 1,
    formCues: [
      "Lean forward a little to work the chest more",
      "Lower until your shoulders dip just below your elbows",
      "Keep your shoulder blades down, away from your ears",
    ],
    substitutes: ["pushup", "dumbbell-shoulder-press"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Dip</title><line x1="40" y1="64" x2="40" y2="120" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><line x1="110" y1="64" x2="110" y2="120" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="75" cy="46" r="11"/><line x1="75" y1="57" x2="75" y2="120"/><path d="M75 66 L52 66 L40 84"/><path d="M75 66 L98 66 L110 84"/><path d="M75 120 L66 146 L78 156"/><path d="M75 120 L86 146 L96 154"/></g></svg>`,
  },

  // ── PULL ──────────────────────────────────────────────────────────
  {
    id: "deadlift",
    name: "Conventional Deadlift",
    equipment: "barbell",
    primary: ["hamstrings", "glutes", "back"],
    secondary: ["forearms", "core"],
    pattern: "hinge",
    isCompound: true,
    tier: 1,
    formCues: [
      "Tighten your stomach and take the slack out of the bar before you pull",
      "Push the floor away with your feet. Don't jerk the bar up",
      "Keep the bar touching your legs the whole way up",
      "Finish standing tall by squeezing your glutes",
    ],
    substitutes: ["trap-bar-deadlift", "rdl", "kettlebell-swing"],
    safety: "Stop if you feel any sharp pain in your lower back. A lifting belt helps on your heaviest sets.",
  },
  {
    id: "rdl",
    name: "Romanian Deadlift",
    equipment: "barbell",
    primary: ["hamstrings", "glutes"],
    secondary: ["back"],
    pattern: "hinge",
    isCompound: true,
    tier: 1,
    formCues: [
      "Push your hips backward, not straight down",
      "Keep a slight, soft bend in your knees the whole time",
      "Stop when you feel a strong stretch down the back of your thighs",
    ],
    substitutes: ["deadlift", "kettlebell-swing"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Romanian Deadlift</title><line x1="66" y1="128" x2="98" y2="128" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="44" cy="60" r="11"/><line x1="54" y1="66" x2="104" y2="86"/><line x1="82" y1="76" x2="82" y2="128"/><path d="M104 86 L102 130 L96 176"/></g></svg>`,
  },
  {
    id: "pullup",
    name: "Pull-Up",
    equipment: "bodyweight",
    primary: ["back"],
    secondary: ["biceps", "forearms"],
    pattern: "vertical-pull",
    isCompound: true,
    tier: 1,
    formCues: [
      "Start hanging fully, with your shoulders active, not loose",
      "Pull your chest up toward the bar",
      "Squeeze your shoulder blades together at the top",
    ],
    substitutes: ["lat-pulldown", "inverted-row"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Pull-Up</title><line x1="32" y1="34" x2="118" y2="34" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M62 34 L56 58 L75 74"/><path d="M88 34 L94 58 L75 74"/><circle cx="75" cy="60" r="11"/><line x1="75" y1="74" x2="75" y2="126"/><path d="M75 126 L64 168"/><path d="M75 126 L86 168"/></g></svg>`,
  },
  {
    id: "lat-pulldown",
    name: "Lat Pulldown",
    equipment: "cable",
    primary: ["back"],
    secondary: ["biceps"],
    pattern: "vertical-pull",
    isCompound: true,
    tier: 2,
    formCues: [
      "Pull the bar down to your upper chest",
      "Lead with your elbows, driving them down and back",
      "Stay mostly upright. Don't lean way back",
    ],
  },
  {
    id: "barbell-row",
    name: "Barbell Row",
    equipment: "barbell",
    primary: ["back"],
    secondary: ["biceps", "forearms"],
    pattern: "horizontal-pull",
    isCompound: true,
    tier: 1,
    formCues: [
      "Bend forward to about 45 degrees, keeping your back flat",
      "Pull the bar to the bottom of your chest",
      "Squeeze your shoulder blades together at the top",
    ],
    substitutes: ["dumbbell-row", "cable-row"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Barbell Row</title><line x1="52" y1="120" x2="92" y2="120" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="44" cy="64" r="11"/><line x1="54" y1="70" x2="110" y2="90"/><path d="M74 78 L80 62 L72 118"/><line x1="60" y1="118" x2="88" y2="118"/><path d="M110 90 L110 130 L102 176"/><path d="M110 130 L120 176"/></g></svg>`,
  },
  {
    id: "dumbbell-row",
    name: "One-Arm Dumbbell Row",
    equipment: "dumbbell",
    primary: ["back"],
    secondary: ["biceps"],
    pattern: "horizontal-pull",
    isCompound: true,
    tier: 1,
    formCues: [
      "Steady yourself with one knee and one hand on a bench",
      "Pull the dumbbell back toward your hip, not up to your chest",
      "Lower it slowly, letting your arm stretch all the way down",
    ],
    substitutes: ["barbell-row", "lat-pulldown"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Single-Arm KB Row</title><line x1="28" y1="124" x2="96" y2="124" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="40" cy="80" r="11"/><line x1="50" y1="86" x2="110" y2="102"/><line x1="58" y1="91" x2="60" y2="124"/><line x1="94" y1="99" x2="96" y2="140"/><circle cx="96" cy="146" r="6" stroke-opacity="0.55"/><line x1="110" y1="102" x2="118" y2="176"/></g></svg>`,
  },

  // ── SQUAT ─────────────────────────────────────────────────────────
  {
    id: "back-squat",
    name: "Barbell Back Squat",
    equipment: "barbell",
    primary: ["quads", "glutes"],
    secondary: ["hamstrings", "core"],
    pattern: "squat",
    isCompound: true,
    tier: 1,
    formCues: [
      "Tighten your stomach before you go down",
      "Sit back and down, letting your knees travel out over your toes",
      "Go down until your hips are at least level with your knees",
      "Push up through the middle of your feet to stand",
    ],
    substitutes: ["front-squat", "goblet-squat", "leg-press"],
    safety: "Set up in a power rack or with safety bars in case you can't finish a rep.",
  },
  {
    id: "front-squat",
    name: "Barbell Front Squat",
    equipment: "barbell",
    primary: ["quads"],
    secondary: ["glutes", "core"],
    pattern: "squat",
    isCompound: true,
    tier: 1,
    formCues: [
      "Keep your elbows up high so the bar stays put on your shoulders",
      "Stay tall with your chest up",
      "Drop straight down between your feet",
    ],
    substitutes: ["back-squat", "goblet-squat"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Tempo Front Squat</title><line x1="54" y1="52" x2="96" y2="52" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><line x1="22" y1="172" x2="128" y2="172" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="75" cy="42" r="11"/><line x1="75" y1="53" x2="74" y2="108"/><path d="M74 62 L92 54 L78 52"/><path d="M74 62 L58 54 L72 52"/><path d="M74 108 L98 120 L92 168"/><path d="M74 108 L50 120 L56 168"/></g></svg>`,
  },
  {
    id: "goblet-squat",
    name: "Goblet Squat",
    equipment: "dumbbell",
    primary: ["quads", "glutes"],
    secondary: ["core"],
    pattern: "squat",
    isCompound: true,
    tier: 2,
    formCues: [
      "Hold the dumbbell against your upper chest",
      "Drop straight down between your feet",
      "Let your elbows brush the inside of your knees at the bottom",
    ],
    substitutes: ["front-squat", "bulgarian-split-squat"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Goblet Squat</title><line x1="22" y1="172" x2="128" y2="172" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="75" cy="42" r="11"/><line x1="75" y1="53" x2="74" y2="106"/><path d="M75 64 L66 84 L74 90"/><path d="M75 64 L84 84 L76 90"/><circle cx="75" cy="95" r="6" stroke-opacity="0.55"/><path d="M74 106 L96 118 L90 166"/><path d="M74 106 L52 118 L58 166"/></g></svg>`,
  },
  {
    id: "bulgarian-split-squat",
    name: "Bulgarian Split Squat",
    equipment: "dumbbell",
    primary: ["quads", "glutes"],
    secondary: ["hamstrings"],
    pattern: "lunge",
    isCompound: true,
    tier: 1,
    formCues: [
      "Rest the top of your back foot on a bench",
      "Drop straight down. Don't lean forward into a lunge",
      "Keep your front shin fairly upright",
    ],
    substitutes: ["goblet-squat", "back-squat"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>KB Bulgarian Split Squat</title><line x1="24" y1="122" x2="46" y2="122" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><line x1="22" y1="172" x2="128" y2="172" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="74" cy="46" r="11"/><line x1="74" y1="57" x2="74" y2="110"/><path d="M74 66 L66 92 L74 96"/><path d="M74 66 L82 92 L74 96"/><circle cx="74" cy="102" r="6" stroke-opacity="0.55"/><path d="M74 110 L90 134 L90 168"/><path d="M74 110 L56 130 L40 122"/></g></svg>`,
  },

  // ── ISOLATION ─────────────────────────────────────────────────────
  {
    id: "barbell-curl",
    name: "Barbell Curl",
    equipment: "barbell",
    primary: ["biceps"],
    secondary: ["forearms"],
    pattern: "isolation",
    isCompound: false,
    tier: 2,
    formCues: [
      "Keep your elbows tucked at your sides",
      "Squeeze your biceps at the top",
      "Lower slowly. Don't just drop it",
    ],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Barbell Curl</title><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><line x1="56" y1="88" x2="94" y2="88" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="75" cy="44" r="11"/><line x1="75" y1="55" x2="75" y2="114"/><path d="M75 64 L70 92 L60 88"/><path d="M75 64 L80 92 L90 88"/><path d="M75 114 L66 148 L66 176"/><path d="M75 114 L84 148 L84 176"/></g></svg>`,
  },
  {
    id: "tricep-pushdown",
    name: "Cable Tricep Pushdown",
    equipment: "cable",
    primary: ["triceps"],
    secondary: [],
    pattern: "isolation",
    isCompound: false,
    tier: 2,
    formCues: [
      "Keep your elbows tucked at your sides",
      "Straighten your arms all the way at the bottom",
      "Stand tall. Don't lean over the bar",
    ],
  },
  {
    id: "lateral-raise",
    name: "Dumbbell Lateral Raise",
    equipment: "dumbbell",
    primary: ["shoulders"],
    secondary: [],
    pattern: "isolation",
    isCompound: false,
    tier: 1,
    formCues: [
      "Keep a slight bend in your elbows the whole time",
      "Lead the movement with your elbows, not your hands",
      "Stop when your arms reach shoulder height, no higher",
    ],
    substitutes: ["face-pull", "dumbbell-shoulder-press"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>KB Lateral Raise</title><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="75" cy="46" r="11"/><line x1="75" y1="57" x2="75" y2="112"/><path d="M75 66 L104 60 L114 62"/><circle cx="120" cy="64" r="6" stroke-opacity="0.55"/><path d="M75 66 L62 88 L64 108"/><path d="M75 112 L65 148 L65 176"/><path d="M75 112 L85 148 L85 176"/></g></svg>`,
  },
  {
    id: "face-pull",
    name: "Cable Face Pull",
    equipment: "cable",
    primary: ["shoulders"],
    secondary: ["back"],
    pattern: "isolation",
    isCompound: false,
    tier: 1,
    formCues: [
      "Pull the rope toward your face, hands ending up by your ears",
      "At the end, rotate your hands back like you're showing off your biceps",
      "Go light with higher reps. This one's for healthy shoulders",
    ],
    substitutes: ["lateral-raise", "barbell-row"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Face Pull</title><line x1="110" y1="46" x2="128" y2="46" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="58" cy="46" r="11"/><line x1="58" y1="57" x2="58" y2="114"/><path d="M58 64 L84 52 L72 40"/><path d="M58 66 L84 78 L72 52"/><path d="M84 52 L110 48"/><path d="M84 78 L110 50"/><path d="M58 114 L49 148 L49 176"/><path d="M58 114 L67 148 L67 176"/></g></svg>`,
  },
  {
    id: "calf-raise",
    name: "Standing Calf Raise",
    equipment: "machine",
    primary: ["calves"],
    secondary: [],
    pattern: "isolation",
    isCompound: false,
    tier: 2,
    formCues: [
      "Let your heels sink for a good stretch at the bottom",
      "Rise all the way up and hold for a second at the top",
      "No bouncing. Keep it slow and controlled",
    ],
  },
  {
    id: "plank",
    name: "Plank",
    equipment: "bodyweight",
    primary: ["core"],
    secondary: [],
    pattern: "isolation",
    isCompound: false,
    tier: 2,
    formCues: [
      "Squeeze your glutes and tighten your stomach",
      "Keep your body flat. Don't let your hips sag or stick up",
      "Hold good form, quality matters more than a long time",
    ],
    substitutes: ["hanging-leg-raise"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Plank</title><line x1="14" y1="152" x2="142" y2="152" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="34" cy="112" r="11"/><line x1="44" y1="116" x2="126" y2="146"/><path d="M52 118 L48 148 L66 148"/><line x1="126" y1="146" x2="122" y2="150"/></g></svg>`,
  },
  {
    id: "hanging-leg-raise",
    name: "Hanging Leg Raise",
    equipment: "bodyweight",
    primary: ["core"],
    secondary: [],
    pattern: "isolation",
    isCompound: false,
    tier: 1,
    formCues: [
      "Hang from a bar with your shoulders active, not loose",
      "Curl your hips up, not just your legs",
      "Lower slowly. Don't swing",
    ],
    substitutes: ["plank"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Hanging Leg Raise</title><line x1="40" y1="28" x2="110" y2="28" stroke="currentColor" stroke-opacity="0.4" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="66" y1="46" x2="66" y2="30"/><line x1="84" y1="46" x2="84" y2="30"/><circle cx="75" cy="56" r="11"/><line x1="75" y1="67" x2="75" y2="106"/><line x1="75" y1="106" x2="120" y2="100"/><line x1="120" y1="100" x2="130" y2="92"/></g></svg>`,
  },

  // ── KETTLEBELL ────────────────────────────────────────────────────
  {
    id: "kettlebell-swing",
    name: "Kettlebell Swing",
    equipment: "kettlebell",
    primary: ["glutes", "hamstrings"],
    secondary: ["back", "core"],
    pattern: "hinge",
    isCompound: true,
    tier: 1,
    formCues: [
      "Push your hips back to swing it. Don't squat down",
      "Snap your hips forward to send the bell up",
      "Let the bell float up to about chest height, no higher",
      "Let it swing back down between your legs naturally",
    ],
    substitutes: ["rdl", "deadlift"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Kettlebell Swing</title><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="56" cy="52" r="11"/><line x1="58" y1="63" x2="70" y2="110"/><line x1="62" y1="74" x2="110" y2="66"/><circle cx="117" cy="64" r="7" stroke-opacity="0.55"/><path d="M70 110 L64 148 L64 176"/><path d="M70 110 L84 146 L92 176"/></g></svg>`,
  },
  {
    id: "turkish-get-up",
    name: "Turkish Get-Up",
    equipment: "kettlebell",
    primary: ["shoulders", "core"],
    secondary: ["glutes", "quads"],
    pattern: "carry",
    isCompound: true,
    tier: 2,
    formCues: [
      "Keep the bell balanced right over your shoulder the whole time",
      "Move slowly and deliberately through each step",
      "Keep your eyes on the bell as you change positions",
    ],
    safety: "Get comfortable with the movement using no weight first.",
    substitutes: ["farmer-carry", "plank"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Turkish Get-Up</title><line x1="18" y1="156" x2="140" y2="156" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="52" cy="98" r="11"/><line x1="62" y1="104" x2="96" y2="122"/><line x1="64" y1="106" x2="58" y2="154"/><line x1="66" y1="102" x2="72" y2="56"/><circle cx="73" cy="50" r="7" stroke-opacity="0.55"/><path d="M96 122 L108 130 L110 154"/><path d="M96 122 L120 138 L136 152"/></g></svg>`,
  },

  // ── CARRIES ───────────────────────────────────────────────────────
  {
    id: "farmer-carry",
    name: "Farmer's Carry",
    equipment: "dumbbell",
    primary: ["forearms", "core"],
    secondary: ["shoulders"],
    pattern: "carry",
    isCompound: true,
    tier: 1,
    formCues: [
      "Stand tall with your shoulders back and down",
      "Walk slowly with steady, controlled steps",
      "Keep your stomach tight the whole way",
    ],
    substitutes: ["turkish-get-up", "suitcase-carry"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Farmer Carry</title><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="75" cy="40" r="11"/><line x1="75" y1="51" x2="75" y2="108"/><line x1="66" y1="60" x2="60" y2="106"/><line x1="84" y1="60" x2="90" y2="106"/><circle cx="59" cy="114" r="6" stroke-opacity="0.55"/><circle cx="91" cy="114" r="6" stroke-opacity="0.55"/><path d="M75 108 L64 148 L60 176"/><path d="M75 108 L86 146 L92 174"/></g></svg>`,
  },
  {
    id: "suitcase-carry",
    name: "Suitcase Carry",
    equipment: "kettlebell",
    primary: ["core", "forearms"],
    secondary: ["shoulders", "glutes"],
    pattern: "carry",
    isCompound: true,
    tier: 2,
    formCues: [
      "One bell in one hand, hanging straight down like a suitcase",
      "Don't lean away from it, stand square and fight the side bend",
      "Ribs down, shoulders level, slow steady steps",
      "Switch hands and walk the same distance back",
    ],
    substitutes: ["farmer-carry"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Suitcase Carry</title><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="72" cy="40" r="11"/><line x1="72" y1="51" x2="72" y2="108"/><line x1="64" y1="60" x2="58" y2="104"/><line x1="80" y1="60" x2="86" y2="88"/><circle cx="57" cy="112" r="6" stroke-opacity="0.55"/><path d="M72 108 L62 148 L58 176"/><path d="M72 108 L84 146 L90 174"/></g></svg>`,
  },
  {
    id: "ruck",
    name: "Ruck",
    equipment: "bodyweight",
    primary: ["glutes", "core"],
    secondary: ["back", "calves", "shoulders"],
    pattern: "carry",
    isCompound: true,
    tier: 2,
    formCues: [
      "Pack high and tight against your back, straps snug, nothing swinging",
      "Walk tall: chest up, shoulders back, short steady steps",
      "Conversational pace. You should be able to talk the whole way",
      "Build the time before you build the weight",
    ],
    safety:
      "If the pack digs in or your posture starts to break down, drop the weight or cut the walk short, especially in the first week.",
    substitutes: ["farmer-carry"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Ruck</title><line x1="18" y1="178" x2="132" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><rect x="40" y="58" width="26" height="34" rx="4" stroke="currentColor" stroke-opacity="0.5" fill="none" stroke-width="3"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="78" cy="42" r="11"/><line x1="76" y1="53" x2="74" y2="108"/><line x1="76" y1="64" x2="62" y2="92"/><line x1="76" y1="64" x2="90" y2="90"/><path d="M74 108 L60 142 L54 176"/><path d="M74 108 L90 144 L98 172"/></g></svg>`,
  },

  // ── HIS SYSTEM: fixed-implement variations ────────────────────────
  // Movements his own written plan calls for that the base library did not
  // carry. Cues are lifted from his documents wherever he wrote one.
  {
    id: "kb-single-leg-rdl",
    name: "KB Single-Leg RDL",
    equipment: "kettlebell",
    primary: ["hamstrings", "glutes"],
    secondary: ["back", "core"],
    pattern: "hinge",
    isCompound: true,
    tier: 1,
    formCues: [
      "Bell in the hand opposite the leg you're standing on",
      "Push your hips straight back and let the bell run down your shin",
      "Keep your hips level. Don't let the free hip roll open to the ceiling",
      "Slow. Balance beats speed here",
    ],
    substitutes: ["rdl", "kettlebell-swing"],
    safety: "Touch a wall or chair with your free hand if balance is the limit, the hinge is the point, not the wobble.",
    figure: `<svg viewBox="0 0 150 190" role="img"><title>KB Single-Leg RDL</title><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="40" cy="72" r="11"/><line x1="50" y1="78" x2="86" y2="98"/><line x1="60" y1="84" x2="56" y2="126"/><circle cx="55" cy="133" r="6" stroke-opacity="0.55"/><path d="M86 98 L88 140 L88 176"/><path d="M86 98 L108 84 L126 78"/></g></svg>`,
  },
  {
    id: "kb-high-pull",
    name: "KB High Pull",
    equipment: "kettlebell",
    primary: ["shoulders", "back"],
    secondary: ["glutes", "hamstrings"],
    pattern: "horizontal-pull",
    isCompound: true,
    tier: 2,
    formCues: [
      "Start it like a swing, the hips drive the bell, your arm only steers",
      "As the bell floats up, pull your elbow high and back behind you",
      "Keep the bell close to your body the whole way",
      "Let it drop back into the next hinge, don't muscle it down",
    ],
    substitutes: ["rear-delt-fly", "face-pull", "kettlebell-swing"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>KB High Pull</title><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="72" cy="46" r="11"/><line x1="72" y1="57" x2="72" y2="112"/><path d="M72 66 L46 56 L60 74"/><circle cx="66" cy="80" r="7" stroke-opacity="0.55"/><path d="M72 112 L62 146 L62 176"/><path d="M72 112 L84 146 L86 176"/></g></svg>`,
  },
  {
    id: "kb-clean",
    name: "KB Clean",
    equipment: "kettlebell",
    primary: ["glutes", "hamstrings"],
    secondary: ["shoulders", "back", "forearms"],
    pattern: "hinge",
    isCompound: true,
    tier: 2,
    formCues: [
      "Tame the arc. Keep the bell close so it never bangs your wrist",
      "Snap your hips, then guide your hand around the bell rather than letting it flip over",
      "Finish with the bell resting against your forearm, elbow tight to your ribs",
      "Stand tall at the top before the next rep",
    ],
    substitutes: ["kettlebell-swing"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>KB Clean</title><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="74" cy="44" r="11"/><line x1="74" y1="55" x2="74" y2="112"/><path d="M74 66 L60 76 L66 62"/><circle cx="66" cy="56" r="7" stroke-opacity="0.55"/><path d="M74 68 L88 96 L86 112"/><path d="M74 112 L64 146 L64 176"/><path d="M74 112 L86 146 L88 176"/></g></svg>`,
  },
  {
    id: "rear-delt-fly",
    name: "DB Rear Delt Fly",
    equipment: "dumbbell",
    primary: ["shoulders"],
    secondary: ["back"],
    pattern: "isolation",
    isCompound: false,
    tier: 2,
    formCues: [
      "Hinge forward with a flat back, dumbbells hanging under your chest",
      "Raise them out to the sides with a soft, fixed elbow",
      "Lead with your elbows, not your hands",
      "Squeeze for a second at the top, then lower slowly",
    ],
    substitutes: ["face-pull", "kb-high-pull"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>DB Rear Delt Fly</title><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="40" cy="76" r="11"/><line x1="50" y1="82" x2="96" y2="98"/><path d="M62 86 L54 66"/><path d="M64 88 L74 68"/><circle cx="52" cy="60" r="6" stroke-opacity="0.55"/><circle cx="76" cy="62" r="6" stroke-opacity="0.55"/><path d="M96 98 L98 140 L98 176"/><path d="M96 98 L88 140 L86 176"/></g></svg>`,
  },
  {
    id: "db-floor-press",
    name: "DB Floor Press",
    equipment: "dumbbell",
    primary: ["chest", "triceps"],
    secondary: ["shoulders"],
    pattern: "horizontal-push",
    isCompound: true,
    tier: 1,
    formCues: [
      "Lie on the floor, knees bent, feet flat",
      "Lower until the backs of your upper arms touch the floor, then pause",
      "Press straight up and slightly together",
      "Slow on the way down. That's where the work is",
    ],
    substitutes: ["dumbbell-bench", "pushup", "barbell-floor-press"],
    safety: "The floor is your safety stop, no bench and no spotter needed.",
    figure: `<svg viewBox="0 0 150 190" role="img"><title>DB Floor Press</title><line x1="14" y1="150" x2="142" y2="150" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="34" cy="136" r="11"/><line x1="45" y1="140" x2="96" y2="142"/><path d="M60 140 L62 110"/><circle cx="62" cy="102" r="6" stroke-opacity="0.55"/><path d="M96 142 L112 120 L128 146"/></g></svg>`,
  },
  {
    id: "barbell-floor-press",
    name: "Barbell Floor Press",
    equipment: "barbell",
    primary: ["chest", "triceps"],
    secondary: ["shoulders"],
    pattern: "horizontal-push",
    isCompound: true,
    tier: 2,
    formCues: [
      "Lie on the floor with the bar over your chest, knees bent",
      "Lower until your upper arms rest on the floor, pause for a beat",
      "Press straight up, no bounce off the floor",
      "Keep your wrists stacked over your elbows",
    ],
    substitutes: ["db-floor-press", "bench-press", "pushup"],
    safety: "Roll the bar over your hips to set up and finish rather than lifting it past your face.",
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Barbell Floor Press</title><line x1="14" y1="150" x2="142" y2="150" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><line x1="46" y1="104" x2="112" y2="104" stroke="currentColor" stroke-opacity="0.45" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="34" cy="136" r="11"/><line x1="45" y1="140" x2="96" y2="142"/><path d="M58 138 L56 106"/><path d="M72 140 L74 106"/><path d="M96 142 L112 120 L128 146"/></g></svg>`,
  },
  {
    id: "hang-power-clean",
    name: "Hang Power Clean",
    equipment: "barbell",
    primary: ["glutes", "hamstrings", "back"],
    secondary: ["shoulders", "quads"],
    pattern: "hinge",
    isCompound: true,
    tier: 2,
    formCues: [
      "Start from the hang, bar just above your knees, back flat",
      "Explosive hip snap, the bar rides straight up your thighs",
      "Drop under it and catch it in the rack with your elbows high",
      "Stand tall, then reset your back before the next rep",
    ],
    substitutes: ["kettlebell-swing", "deadlift"],
    safety: "Technical lift, groove the sequence with the bare 44 lb bar before you load it.",
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Hang Power Clean</title><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><line x1="42" y1="86" x2="108" y2="86" stroke="currentColor" stroke-opacity="0.45" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="75" cy="46" r="11"/><line x1="75" y1="57" x2="75" y2="112"/><path d="M75 68 L54 86"/><path d="M75 68 L96 86"/><path d="M75 112 L62 144 L62 176"/><path d="M75 112 L88 144 L88 176"/></g></svg>`,
  },
  {
    id: "push-press",
    name: "Push Press",
    equipment: "barbell",
    primary: ["shoulders"],
    secondary: ["triceps", "quads", "core"],
    pattern: "vertical-push",
    isCompound: true,
    tier: 1,
    formCues: [
      "Dip a few inches with your knees, chest stays upright",
      "Drive hard with your legs and let that start the bar",
      "Your arms only finish it, lock out with the bar over your ears",
      "This is not a strict press; the leg drive is the whole point",
    ],
    substitutes: ["ohp", "dumbbell-shoulder-press"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Push Press</title><line x1="44" y1="22" x2="106" y2="22" stroke="currentColor" stroke-opacity="0.45" stroke-width="4" stroke-linecap="round"/><line x1="22" y1="178" x2="128" y2="178" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="75" cy="52" r="11"/><line x1="75" y1="63" x2="75" y2="114"/><path d="M75 72 L58 46 L56 24"/><path d="M75 72 L92 46 L94 24"/><path d="M75 114 L60 138 L62 176"/><path d="M75 114 L90 138 L88 176"/></g></svg>`,
  },
  {
    id: "pike-pushup",
    name: "Pike Push-Up",
    equipment: "bodyweight",
    primary: ["shoulders"],
    secondary: ["triceps", "core"],
    pattern: "vertical-push",
    isCompound: true,
    tier: 2,
    formCues: [
      "Hips high, body in an upside-down V",
      "Lower the top of your head toward the floor between your hands",
      "Elbows track back and slightly out, not flared wide",
      "Press back up until your arms are straight",
    ],
    substitutes: ["dumbbell-shoulder-press", "pushup", "ohp"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Pike Push-Up</title><line x1="14" y1="158" x2="142" y2="158" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="44" cy="120" r="11"/><path d="M52 114 L84 62"/><path d="M84 62 L116 152"/><path d="M50 128 L38 156"/></g></svg>`,
  },
  {
    id: "scapular-pullup",
    name: "Scapular Pull-Up",
    equipment: "bodyweight",
    primary: ["back"],
    secondary: ["forearms", "shoulders"],
    pattern: "vertical-pull",
    isCompound: false,
    tier: 2,
    formCues: [
      "Hang from the bar with straight arms",
      "Without bending your elbows, pull your shoulders down away from your ears",
      "Hold the top for a beat, then let them rise again under control",
      "Small movement, a couple of inches is the whole rep",
    ],
    substitutes: ["dead-hang", "pullup"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Scapular Pull-Up</title><line x1="36" y1="26" x2="114" y2="26" stroke="currentColor" stroke-opacity="0.45" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="64" y1="52" x2="64" y2="28"/><line x1="86" y1="52" x2="86" y2="28"/><circle cx="75" cy="62" r="11"/><line x1="75" y1="73" x2="75" y2="128"/><path d="M75 128 L66 158 L66 178"/><path d="M75 128 L84 158 L84 178"/></g></svg>`,
  },
  {
    id: "dead-hang",
    name: "Dead Hang",
    equipment: "bodyweight",
    primary: ["forearms", "back"],
    secondary: ["shoulders"],
    pattern: "vertical-pull",
    isCompound: false,
    tier: 2,
    formCues: [
      "Hang with straight arms and let your body lengthen",
      "Relax and breathe. This is decompression, not a hold-your-breath test",
      "Keep a firm grip and let go before your hands do",
      "Step off gently rather than dropping",
    ],
    substitutes: ["scapular-pullup"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Dead Hang</title><line x1="36" y1="26" x2="114" y2="26" stroke="currentColor" stroke-opacity="0.45" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="66" y1="58" x2="66" y2="28"/><line x1="84" y1="58" x2="84" y2="28"/><circle cx="75" cy="68" r="11"/><line x1="75" y1="79" x2="75" y2="136"/><line x1="75" y1="136" x2="72" y2="176"/><line x1="75" y1="136" x2="79" y2="176"/></g></svg>`,
  },
  {
    id: "glute-bridge",
    name: "Glute Bridge",
    equipment: "bodyweight",
    primary: ["glutes"],
    secondary: ["hamstrings", "core"],
    pattern: "hinge",
    isCompound: false,
    tier: 2,
    formCues: [
      "Lie on your back, knees bent, heels close to your backside",
      "Push through your heels and squeeze your glutes to lift your hips",
      "Stop where your body makes a straight line. Don't arch past it",
      "Lower slowly and reset",
    ],
    substitutes: ["rdl", "kettlebell-swing"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Glute Bridge</title><line x1="14" y1="156" x2="142" y2="156" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="30" cy="144" r="11"/><path d="M41 140 L84 108"/><path d="M84 108 L104 140 L104 154"/><path d="M44 146 L40 154"/></g></svg>`,
  },
  {
    id: "hollow-hold",
    name: "Hollow Hold",
    equipment: "bodyweight",
    primary: ["core"],
    secondary: [],
    pattern: "isolation",
    isCompound: false,
    tier: 2,
    formCues: [
      "Lie on your back and press your lower back flat into the floor",
      "Lift your shoulders and legs a few inches, arms overhead",
      "If your back lifts off the floor, bend your knees or lower your arms",
      "Breathe. Don't hold your breath to stay tight",
    ],
    substitutes: ["plank", "hanging-leg-raise"],
    figure: `<svg viewBox="0 0 150 190" role="img"><title>Hollow Hold</title><line x1="14" y1="150" x2="142" y2="150" stroke="currentColor" stroke-opacity="0.3" stroke-width="4" stroke-linecap="round"/><g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="52" cy="120" r="11"/><path d="M62 124 Q90 140 118 118"/><path d="M46 112 L20 100"/></g></svg>`,
  },
];

/* ────────────────────────────────────────────────────────────────────
 * Workout templates (split routines)
 * ──────────────────────────────────────────────────────────────────── */

export interface WorkoutTemplate {
  id: string;
  name: string;
  description: string;
  level: "beginner" | "intermediate" | "advanced";
  daysPerWeek: number;
  /** Each day is a list of exercise IDs from EXERCISE_DB */
  days: { name: string; exerciseIds: string[]; targetSets?: number; targetRepsLow?: number; targetRepsHigh?: number }[];
}

export const TEMPLATES: WorkoutTemplate[] = [
  {
    id: "ppl",
    name: "Push / Pull / Legs",
    description:
      "Classic 6-day bodybuilding split. Each muscle group gets 2 sessions per week with great recovery.",
    level: "intermediate",
    daysPerWeek: 6,
    days: [
      {
        name: "Push A",
        exerciseIds: ["bench-press", "ohp", "dip", "lateral-raise", "tricep-pushdown"],
        targetSets: 4,
        targetRepsLow: 6,
        targetRepsHigh: 10,
      },
      {
        name: "Pull A",
        exerciseIds: ["deadlift", "pullup", "barbell-row", "face-pull", "barbell-curl"],
        targetSets: 4,
        targetRepsLow: 6,
        targetRepsHigh: 10,
      },
      {
        name: "Legs A",
        exerciseIds: ["back-squat", "rdl", "bulgarian-split-squat", "calf-raise", "hanging-leg-raise"],
        targetSets: 4,
        targetRepsLow: 6,
        targetRepsHigh: 12,
      },
      {
        name: "Push B",
        exerciseIds: ["dumbbell-bench", "dumbbell-shoulder-press", "pushup", "lateral-raise", "tricep-pushdown"],
        targetSets: 3,
        targetRepsLow: 8,
        targetRepsHigh: 12,
      },
      {
        name: "Pull B",
        exerciseIds: ["rdl", "lat-pulldown", "dumbbell-row", "face-pull", "barbell-curl"],
        targetSets: 3,
        targetRepsLow: 8,
        targetRepsHigh: 12,
      },
      {
        name: "Legs B",
        exerciseIds: ["front-squat", "rdl", "goblet-squat", "calf-raise", "plank"],
        targetSets: 3,
        targetRepsLow: 8,
        targetRepsHigh: 15,
      },
    ],
  },
  {
    id: "upper-lower",
    name: "Upper / Lower",
    description:
      "Balanced 4-day split. Great for intermediate lifters wanting strength and size.",
    level: "intermediate",
    daysPerWeek: 4,
    days: [
      {
        name: "Upper A (Heavy)",
        exerciseIds: ["bench-press", "barbell-row", "ohp", "pullup", "tricep-pushdown", "barbell-curl"],
        targetSets: 4,
        targetRepsLow: 5,
        targetRepsHigh: 8,
      },
      {
        name: "Lower A (Heavy)",
        exerciseIds: ["back-squat", "rdl", "bulgarian-split-squat", "calf-raise", "plank"],
        targetSets: 4,
        targetRepsLow: 5,
        targetRepsHigh: 8,
      },
      {
        name: "Upper B (Volume)",
        exerciseIds: ["dumbbell-bench", "lat-pulldown", "dumbbell-shoulder-press", "dumbbell-row", "lateral-raise", "face-pull"],
        targetSets: 3,
        targetRepsLow: 10,
        targetRepsHigh: 15,
      },
      {
        name: "Lower B (Volume)",
        exerciseIds: ["front-squat", "rdl", "goblet-squat", "calf-raise", "hanging-leg-raise"],
        targetSets: 3,
        targetRepsLow: 10,
        targetRepsHigh: 15,
      },
    ],
  },
  {
    id: "full-body-3day",
    name: "Full Body 3-Day",
    description: "Hit every major muscle group 3 times per week. Ideal for beginners and busy people.",
    level: "beginner",
    daysPerWeek: 3,
    days: [
      {
        name: "Full Body A",
        exerciseIds: ["back-squat", "bench-press", "barbell-row", "plank"],
        targetSets: 3,
        targetRepsLow: 6,
        targetRepsHigh: 10,
      },
      {
        name: "Full Body B",
        exerciseIds: ["deadlift", "ohp", "pullup", "hanging-leg-raise"],
        targetSets: 3,
        targetRepsLow: 6,
        targetRepsHigh: 10,
      },
      {
        name: "Full Body C",
        exerciseIds: ["front-squat", "dumbbell-bench", "dumbbell-row", "face-pull"],
        targetSets: 3,
        targetRepsLow: 8,
        targetRepsHigh: 12,
      },
    ],
  },
  {
    id: "minimalist",
    name: "Minimalist Strength",
    description: "Just the big lifts. 2 days a week, 30 minutes per session. Perfect for busy weeks.",
    level: "beginner",
    daysPerWeek: 2,
    days: [
      {
        name: "Day A",
        exerciseIds: ["back-squat", "bench-press", "barbell-row"],
        targetSets: 5,
        targetRepsLow: 5,
        targetRepsHigh: 5,
      },
      {
        name: "Day B",
        exerciseIds: ["deadlift", "ohp", "pullup"],
        targetSets: 5,
        targetRepsLow: 5,
        targetRepsHigh: 5,
      },
    ],
  },
];

/* ────────────────────────────────────────────────────────────────────
 * Analytics helpers
 * ──────────────────────────────────────────────────────────────────── */

/**
 * Estimate 1-rep max from a working set using the Epley formula.
 * Most accurate for sets in the 1–10 rep range.
 *
 * 1RM = weight × (1 + reps / 30)
 */
export function estimate1RM(weight: number, reps: number): number {
  if (weight <= 0 || reps <= 0) return 0;
  if (reps === 1) return weight;
  return Math.round(weight * (1 + reps / 30) * 10) / 10;
}

/**
 * Calculate the weight you should use to hit a target rep count at a
 * given percentage of your 1RM. Useful for picking working weights.
 */
export function weightForReps(oneRM: number, targetReps: number): number {
  // Inverted Epley: w = 1RM / (1 + reps/30)
  if (oneRM <= 0 || targetReps <= 0) return 0;
  return Math.round((oneRM / (1 + targetReps / 30)) * 2) / 2; // round to nearest 0.5
}

/**
 * RPE-to-RIR mapping. RPE 10 = max effort, RPE 6 = 4 reps in reserve.
 */
export const RPE_RIR: Record<number, number> = {
  10: 0,
  9.5: 0,
  9: 1,
  8.5: 1,
  8: 2,
  7.5: 2,
  7: 3,
  6.5: 3,
  6: 4,
  5: 5,
};

export function rpeToRir(rpe: number): number {
  return RPE_RIR[rpe] ?? Math.max(0, Math.round(10 - rpe));
}

/**
 * Volume landmarks per muscle group, in working sets per week.
 * Based on Israetel's Renaissance Periodization recommendations.
 *
 *   MV  = Maintenance Volume
 *   MEV = Minimum Effective Volume (start of growth)
 *   MAV = Maximum Adaptive Volume (sweet spot)
 *   MRV = Maximum Recoverable Volume (overload ceiling)
 */
export const VOLUME_LANDMARKS: Record<MuscleGroup, { mv: number; mev: number; mav: number; mrv: number }> = {
  chest:      { mv: 8,  mev: 10, mav: 16, mrv: 22 },
  back:       { mv: 8,  mev: 10, mav: 18, mrv: 25 },
  shoulders:  { mv: 8,  mev: 8,  mav: 16, mrv: 26 },
  biceps:     { mv: 5,  mev: 8,  mav: 14, mrv: 20 },
  triceps:    { mv: 4,  mev: 6,  mav: 14, mrv: 18 },
  quads:      { mv: 6,  mev: 8,  mav: 14, mrv: 20 },
  hamstrings: { mv: 4,  mev: 6,  mav: 12, mrv: 18 },
  glutes:     { mv: 4,  mev: 6,  mav: 12, mrv: 16 },
  calves:     { mv: 6,  mev: 8,  mav: 14, mrv: 20 },
  core:       { mv: 0,  mev: 6,  mav: 16, mrv: 25 },
  forearms:   { mv: 0,  mev: 4,  mav: 10, mrv: 16 },
};

export function getExercise(id: string): Exercise | undefined {
  return EXERCISE_DB.find((e) => e.id === id);
}
