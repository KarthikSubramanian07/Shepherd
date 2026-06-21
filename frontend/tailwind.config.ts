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
        // "Daybreak Watch" — a light, pastoral oversight console.
        // Wool off-white ground; warmth carried by the orange lantern + brown,
        // NOT by a beige page. (Token names kept stable so components flip in place.)
        canvas: "#f7f5f1",   // app ground — wool off-white, near-neutral
        panel: "#fffefb",    // raised cards / panels
        panel2: "#f0ece4",   // inset / secondary fills, rails
        edge: "#e2dcd0",     // hairlines / borders
        ink: "#2a231d",      // primary text — warm charcoal
        muted: "#7c7064",    // secondary text — warm taupe (≥4.5:1 on ground)
        accent: "#dd6a1f",   // the lantern — identity + the attention moment
        bark: "#7a5c44",     // earthy brown — structure, brand glyph, secondary
        // Status semantics — hue + icon/label everywhere (never color alone).
        ok: "#1f8a5b",       // safe / completed — meadow green
        flag: "#dd6a1f",     // needs attention — unified with the lantern
        halt: "#cf3b34",     // stopped / dangerous — clay red
        idle: "#9a8f81",     // pending / inactive — warm gray
        running: "#dd6a1f",  // active — the lantern
      },
      boxShadow: {
        // Soft daylight lift, not glow. Lantern glow reserved for the active node.
        card: "0 1px 2px rgba(42,35,29,0.04), 0 8px 24px -16px rgba(42,35,29,0.18)",
        lift: "0 2px 4px rgba(42,35,29,0.05), 0 16px 36px -18px rgba(42,35,29,0.24)",
        lantern: "0 0 0 1px rgba(221,106,31,0.55), 0 0 24px rgba(221,106,31,0.28)",
        halt: "0 0 0 2px rgba(207,59,52,0.6), 0 0 26px rgba(207,59,52,0.28)",
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
