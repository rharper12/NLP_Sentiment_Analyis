// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReviewChoice } from "./ReviewChoice";

afterEach(cleanup);

it("recommends priority review after Comprehend and sizes only unreviewed posts", () => {
  const choose = vi.fn();
  render(<ReviewChoice total={500} unreviewed={200} machineScored={500} onChoose={choose} onBack={vi.fn()} />);
  expect(screen.getByRole<HTMLInputElement>("radio", { name: /Lowest confidence/ }).checked).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "%" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "10" } });
  fireEvent.click(screen.getByRole("button", { name: "Start reviewing 20 posts" }));
  expect(choose).toHaveBeenCalledWith("low_confidence", 20, "count");
  fireEvent.click(screen.getByRole("radio", { name: /Review a sample/ }));
  fireEvent.click(screen.getByRole("button", { name: "Start reviewing 50 posts" }));
  expect(choose).toHaveBeenLastCalledWith("sample", 50, "count");
});

it("does not offer an empty priority queue as actionable", () => {
  render(<ReviewChoice total={500} unreviewed={0} machineScored={500} onChoose={vi.fn()} onBack={vi.fn()} />);
  fireEvent.click(screen.getByRole("radio", { name: /Lowest confidence/ }));
  expect(screen.getByRole("button", { name: /Start reviewing/ }).hasAttribute("disabled")).toBe(true);
});
