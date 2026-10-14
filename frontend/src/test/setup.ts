import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

// Tests must never reach a real server (a local pilot stack on :8000 answers
// 401 to fake tokens, which logs the test user out). Tests that need
// responses stub fetch or the api functions themselves.
globalThis.fetch = (() => Promise.reject(new TypeError("no network in tests"))) as typeof fetch;
