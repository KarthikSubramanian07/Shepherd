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
        // Command-center dark palette
        canvas: "#0b0e14",
        panel: "#11151f",
        panel2: "#161b27",
        edge: "#222a39",
        ink: "#e6edf3",
        muted: "#8b97a8",
        accent: "#3b82f6",
        // Status semantics shared across graph + agents
        ok: "#22c55e",
        flag: "#f59e0b",
        halt: "#ef4444",
        idle: "#64748b",
        running: "#3b82f6",
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(59,130,246,0.4), 0 0 24px rgba(59,130,246,0.25)",
        halt: "0 0 0 2px rgba(239,68,68,0.7), 0 0 28px rgba(239,68,68,0.35)",
      },
      keyframes: {
        pulseRing: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
      },
      animation: {
        pulseRing: "pulseRing 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
