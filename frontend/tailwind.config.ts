import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/app/**/*.{ts,tsx}",
    "./src/components/**/*.{ts,tsx}",
    "./src/lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Palette distilled from the Shepherd logo: deep pine-teal mark on a warm
        // peach ground. Teal carries the brand; a terracotta "lantern" carries the
        // moment that needs a human. (Token names kept stable.)
        canvas: "#f6ebdd",   // app ground — warm peach-cream (lightened from logo bg)
        panel: "#fffdf8",    // raised cards / panels — warm near-white
        panel2: "#f0e2d2",   // inset / secondary fills, rails — peach
        edge: "#e4d2bf",     // hairlines / borders — warm tan
        ink: "#223b3a",      // primary text — deep pine-teal (the brand mark color)
        muted: "#5f7269",    // secondary text — desaturated teal-gray (≥4.5:1)
        accent: "#cf6a43",   // PRIMARY interactive — terracotta (CTAs, active, links).
                             // Warm pop against teal+peach; the brand's complement.
        "accent-ink": "#a8502e", // terracotta darkened for small TEXT on light (AA)
        teal: "#2c6e60",     // brand secondary — calm pine-teal (the "on watch" tone)
        lantern: "#cf6a43",  // attention/intervention signal — same terracotta
        bark: "#8a6f57",     // warm tertiary brown (rare structural use)
        // Status semantics — hue + icon/label everywhere (never color alone).
        // Only ONE green now (ok), so it never reads as a second teal.
        ok: "#3f8f4e",       // safe / completed — leaf green (distinct from teal)
        flag: "#cf6a43",     // needs attention — terracotta
        halt: "#bb4a3a",     // stopped / dangerous — warm clay red (softened to fit)
        idle: "#9a8b7a",     // pending / inactive — warm taupe
        running: "#cf6a43",  // active — terracotta
      },
      boxShadow: {
        // Soft daylight lift. Lantern glow (terracotta) reserved for the active node.
        card: "0 1px 2px rgba(34,59,58,0.05), 0 8px 24px -16px rgba(34,59,58,0.20)",
        lift: "0 2px 4px rgba(34,59,58,0.06), 0 16px 36px -18px rgba(34,59,58,0.26)",
        lantern: "0 0 0 1px rgba(207,106,67,0.55), 0 0 24px rgba(207,106,67,0.30)",
        halt: "0 0 0 2px rgba(192,70,60,0.6), 0 0 26px rgba(192,70,60,0.28)",
      },
      fontFamily: {
        // Anthropic/Claude aesthetic: a warm literary serif for display + a clean
        // humanist sans for UI (system stacks → no network, renders crisp on macOS;
        // deliberately NOT Arial/Helvetica). Mono for data/IDs/traces.
        serif: [
          "Iowan Old Style", "Palatino Linotype", "Palatino",
          "Hoefler Text", "Georgia", "ui-serif", "serif",
        ],
        sans: [
          "-apple-system", "BlinkMacSystemFont", "SF Pro Text", "Segoe UI Variable",
          "Inter", "ui-sans-serif", "system-ui", "sans-serif",
        ],
        mono: [
          "ui-monospace", "SF Mono", "JetBrains Mono", "Menlo", "Consolas", "monospace",
        ],
      },
      letterSpacing: {
        eyebrow: "0.14em",
      },
      transitionTimingFunction: {
        "out-quart": "cubic-bezier(0.25, 1, 0.5, 1)",
        "out-quint": "cubic-bezier(0.22, 1, 0.36, 1)",
        "out-expo": "cubic-bezier(0.16, 1, 0.3, 1)",
      },
      keyframes: {
        pulseRing: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.4" },
        },
        watch: {
          "0%, 100%": { opacity: "0.95", transform: "scale(1)" },
          "50%": { opacity: "0.5", transform: "scale(0.8)" },
        },
        // The signature moment: the intervention banner rises into view.
        riseIn: {
          from: { opacity: "0", transform: "translateY(8px) scale(0.99)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        drawCheck: {
          from: { strokeDashoffset: "24" },
          to: { strokeDashoffset: "0" },
        },
      },
      animation: {
        pulseRing: "pulseRing 1.6s ease-in-out infinite",
        watch: "watch 2.4s ease-in-out infinite",
        riseIn: "riseIn 0.32s cubic-bezier(0.16, 1, 0.3, 1) both",
      },
    },
  },
  plugins: [],
};

export default config;
