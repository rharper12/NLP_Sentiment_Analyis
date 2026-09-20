// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { useEffect } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { StepInfo } from "../../api/types";
import { usePipelineConfig } from "../../hooks/usePipelineConfig";
import { CleanStep } from "./CleanStep";

// A backend regression asserts this shared fixture exactly matches the real /steps response.
const steps = JSON.parse(readFileSync("../backend/tests/fixtures/steps.json", "utf8")) as StepInfo[];
const submit = vi.fn();
function Harness() {
  const { config, dispatch, activeSteps } = usePipelineConfig();
  useEffect(() => { dispatch({ type: "init", steps }); }, [dispatch]);
  return <CleanStep steps={steps} config={config} busy={false} onToggle={(name) => dispatch({ type: "toggle", name })} onMove={(name, direction) => dispatch({ type: "move", name, direction })} onOptions={vi.fn()} onExplain={vi.fn()} onBack={vi.fn()} onRun={() => submit(activeSteps)} />;
}
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it.each(steps.flatMap((step, index) => ([-1, 1] as const).map((direction) => ({ step, index, direction }))))("matches display and submitted order when moving $step.name $direction", ({ step, index, direction }) => {
  const { container } = render(<Harness />);
  const displayed = () => [...container.querySelectorAll("li > label[aria-label]")].map((label) => label.getAttribute("aria-label"));
  expect(displayed()).toEqual(steps.map((s) => s.title));
  const move = screen.getByRole("button", { name: `Move ${step.title} ${direction === -1 ? "up" : "down"}` });
  const neighbour = steps[index + direction];
  const allowed = !!neighbour && neighbour.group === step.group;
  expect(move.hasAttribute("disabled")).toBe(!allowed);
  fireEvent.click(move);
  const expected = [...steps];
  if (allowed) [expected[index], expected[index + direction]] = [expected[index + direction], expected[index]];
  expect(displayed()).toEqual(expected.map((s) => s.title));
  expect(expected.at(-1)?.name).toBe("missing_data");
  fireEvent.click(screen.getAllByRole("button", { name: /Run pipeline and measure/ })[0]);
  expect(submit).toHaveBeenLastCalledWith(expected.map((s) => s.name));
});
