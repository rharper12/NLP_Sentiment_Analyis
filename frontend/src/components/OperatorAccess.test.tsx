// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { api, AUTH_REQUIRED } from "../api/client";
import { OperatorAccess } from "./OperatorAccess";

vi.mock("../api/client", async (original) => ({
  ...await original<typeof import("../api/client")>(),
  api: { health: vi.fn(), login: vi.fn() },
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("clears the entered key, waits for authorization, and preserves work on session expiry", async () => {
  vi.mocked(api.health).mockResolvedValue({ status: "ok", version: "test", diagnostics: false, x_configured: false, auth_required: true });
  let finishLogin: () => void = () => {};
  vi.mocked(api.login).mockImplementation(() => new Promise<void>((resolve) => { finishLogin = resolve; }));
  render(<OperatorAccess><p>Current pipeline work</p></OperatorAccess>);
  const input = await screen.findByLabelText<HTMLInputElement>("Operator key");
  expect(screen.queryByText("Current pipeline work")).toBeNull();
  fireEvent.change(input, { target: { value: "test-only-operator-key" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(input.value).toBe("");
  expect(api.login).toHaveBeenCalledWith("test-only-operator-key");
  expect(screen.queryByText("Current pipeline work")).toBeNull();
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
  await act(async () => finishLogin());
  expect(screen.getByText("Current pipeline work")).toBeTruthy();
  act(() => window.dispatchEvent(new Event(AUTH_REQUIRED)));
  expect(screen.getByLabelText("Operator key")).toBeTruthy();
  expect(screen.getByText("Current pipeline work").parentElement?.hasAttribute("inert")).toBe(true);
});

it("keeps unconfigured local development accessible", async () => {
  vi.mocked(api.health).mockResolvedValue({ status: "ok", version: "test", diagnostics: true, x_configured: false, auth_required: false });
  render(<OperatorAccess><p>Local workflow</p></OperatorAccess>);
  expect(await screen.findByText("Local workflow")).toBeTruthy();
  expect(api.login).not.toHaveBeenCalled();
});

it("shows an accessible skeleton on reload until the connection check finishes", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.health>>) => void;
  vi.mocked(api.health).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  render(<OperatorAccess><p>Ready workflow</p></OperatorAccess>);
  expect(screen.getByRole("status").textContent).toBe("Loading Sentiment Prep…");
  expect(screen.getByRole("main").getAttribute("aria-busy")).toBe("true");
  expect(screen.queryByText("Ready workflow")).toBeNull();
  await act(async () => finish({ status: "ok", version: "test", diagnostics: true, x_configured: false, auth_required: false }));
  expect(screen.getByText("Ready workflow")).toBeTruthy();
  expect(screen.queryByText("Loading Sentiment Prep…")).toBeNull();
  expect(api.login).not.toHaveBeenCalled();
});
