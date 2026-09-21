// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import DiffDialog from "./DiffDialog";

const originalShow = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, "showModal");
const originalClose = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, "close");
beforeEach(() => {
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: { configurable: true, value(this: HTMLDialogElement) { this.open = true; } },
    close: { configurable: true, value(this: HTMLDialogElement) { this.open = false; } },
  });
});
afterEach(() => {
  cleanup();
  for (const [name, descriptor] of [["showModal", originalShow], ["close", originalClose]] as const) {
    if (descriptor) Object.defineProperty(HTMLDialogElement.prototype, name, descriptor);
    else Reflect.deleteProperty(HTMLDialogElement.prototype, name);
  }
});

it("removes an Escape-dismissed selection before immediate reopening or a delayed close event", () => {
  function Harness() {
    const [selected, setSelected] = useState(false);
    return <>
      <button onClick={() => setSelected(true)}>Inspect</button>
      {selected && <DiffDialog pair={{ original: { id: "r1", text: "Hello", source_type: "csv" }, processed: null }} onClose={() => setSelected(false)} />}
    </>;
  }
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "Inspect" }));
  const first = screen.getByRole("dialog");
  const cancel = new Event("cancel", { cancelable: true });
  fireEvent(first, cancel);
  expect(cancel.defaultPrevented).toBe(true);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Inspect" }));
  const second = screen.getByRole("dialog");
  expect(second).not.toBe(first);
  fireEvent(first, new Event("close"));
  expect(screen.getByRole("dialog")).toBe(second);
});

it("highlights casing and punctuation using the actual processed text", () => {
  const { container } = render(<DiffDialog pair={{
    original: { id: "r1", text: "I LOVED it!", source_type: "csv" },
    processed: { id: "r1", text: "I loved it", tokens: ["different", "tokens"], source_type: "csv" },
  }} onClose={() => {}} />);
  expect([...container.querySelectorAll("del")].map((node) => node.textContent)).toEqual(["LOVED", "!"]);
  expect([...container.querySelectorAll("ins")].map((node) => node.textContent)).toEqual(["loved"]);
  expect(screen.queryByText("different")).toBeNull();
});

it("makes whitespace-only edits visible", () => {
  render(<DiffDialog pair={{
    original: { id: "r1", text: "hello  world", source_type: "csv" },
    processed: { id: "r1", text: "hello world", source_type: "csv" },
  }} onClose={() => {}} />);
  expect(screen.getByTitle("Removed whitespace").textContent).toBe("··");
  expect(screen.getByTitle("Added whitespace").textContent).toBe("·");
});
