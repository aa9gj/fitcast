import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: [
          "var(--font-mono)",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
      colors: {
        ink: {
          DEFAULT: "#0d1117",
          soft: "#161b22",
          line: "#21262d",
        },
        accent: {
          DEFAULT: "#2f81f7",
          soft: "#1f6feb",
        },
        verdict: {
          qualified: "#2ea043",
          stretch: "#bb8009",
          no: "#cf222e",
        },
      },
    },
  },
  plugins: [],
};

export default config;
