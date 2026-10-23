import { defineConfig, mergeConfig } from "vite";
import baseConfig from "./vite.config";

export default mergeConfig(
  baseConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      globals: false,
      reporters: ["default", "junit"],
      outputFile: { junit: "reports/junit.xml" },
      css: false,
      coverage: {
        provider: "v8",
        reporter: ["text", "html", "lcov", "cobertura"],
        include: ["src/**/*.{ts,tsx}"],
        exclude: ["src/main.tsx", "src/vite-env.d.ts", "src/test/**"],
        // 96% statements / 91% branches / 85% functions on CI when these floors
        // were set (2026-09-26). They sit a few points below as a regression
        // guard, not a target; the backend's --cov-fail-under in ci.yml works
        // the same way.
        thresholds: {
          statements: 90,
          branches: 85,
          functions: 80,
          lines: 90,
        },
      },
    },
  })
);
