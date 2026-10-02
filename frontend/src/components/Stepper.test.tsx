// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { Stepper } from "./Stepper";

afterEach(cleanup);

it("prevents stage navigation while manual review owns unsaved decisions", () => {
  const select = vi.fn();
  const { rerender } = render(<Stepper current="label" reached="export" disabled onSelect={select} />);
  for (const button of screen.getAllByRole("button")) {
    expect(button.hasAttribute("disabled")).toBe(true);
    fireEvent.click(button);
  }
  expect(select).not.toHaveBeenCalled();
  rerender(<Stepper current="label" reached="export" onSelect={select} />);
  fireEvent.click(screen.getByRole("button", { name: /Export/ }));
  expect(select).toHaveBeenCalledWith("export");
});

it("shows consumer review as step two and unlocks later stages in that order", () => {
  const select = vi.fn();
  render(<Stepper consumer current="label" reached="label" onSelect={select} />);
  const buttons = screen.getAllByRole("button");
  expect(buttons.map((button) => button.textContent)).toEqual([
    expect.stringContaining("Collect"), expect.stringContaining("2Review & label"),
    expect.stringContaining("3Clean"), expect.stringContaining("4Analyze"),
    expect.stringContaining("5Export"),
  ]);
  expect(buttons[1].getAttribute("aria-current")).toBe("step");
  for (const button of buttons.slice(2)) expect(button.hasAttribute("disabled")).toBe(true);
});
