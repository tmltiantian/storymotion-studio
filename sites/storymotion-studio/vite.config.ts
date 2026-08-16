import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": process.env.STORYMOTION_API_URL ?? "http://127.0.0.1:8787",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    css: true,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
