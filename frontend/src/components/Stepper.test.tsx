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
