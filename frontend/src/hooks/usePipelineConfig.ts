// The ordered, toggleable list of steps plus per-step options. A reducer keeps every mutation
// explicit so the "changes not yet applied" indicator can compare against the last run.

import { useReducer } from "react";

import type { StepInfo, StepOptions } from "../api/types";

export interface PipelineConfig {
  order: string[];
  groups: { [name: string]: StepInfo["group"] };
  enabled: { [name: string]: boolean };
  options: StepOptions;
  explain: boolean;
}

type Action =
  | { type: "init"; steps: StepInfo[] }
  | { type: "toggle"; name: string }
  | { type: "move"; name: string; direction: -1 | 1 }
  | { type: "options"; options: Partial<StepOptions> }
  | { type: "explain"; value: boolean };

/** Shared by the controls and reducer; movement stays within the server-provided group. */
export function canMove(state: PipelineConfig, name: string, direction: -1 | 1): boolean {
  const index = state.order.indexOf(name), target = index + direction;
  return index >= 0 && target >= 0 && target < state.order.length
    && state.groups[name] === state.groups[state.order[target]];
}

function reduce(state: PipelineConfig, action: Action): PipelineConfig {
  switch (action.type) {
    case "init":
      return {
        ...state,
        order: action.steps.map((s) => s.name),
        groups: Object.fromEntries(action.steps.map((s) => [s.name, s.group])),
        enabled: Object.fromEntries(action.steps.map((s) => [s.name, true])),
      };
    case "toggle":
      return { ...state, enabled: { ...state.enabled, [action.name]: !state.enabled[action.name] } };
    case "move": {
      // Cleaning steps always run before normalisation steps, so moves stay within a group.
      const index = state.order.indexOf(action.name);
      const target = index + action.direction;
      if (!canMove(state, action.name, action.direction)) return state;
      const order = [...state.order];
      [order[index], order[target]] = [order[target], order[index]];
      return { ...state, order };
    }
    case "options":
      return { ...state, options: { ...state.options, ...action.options } };
    case "explain":
      return { ...state, explain: action.value };
  }
}

const initialConfig: PipelineConfig = {
  order: [],
  groups: {},
  enabled: {},
  options: { missing_data_strategy: "drop", missing_data_fill_value: "[EMPTY]", keep_negations: true },
  explain: true,
};

export function usePipelineConfig() {
  const [config, dispatch] = useReducer(reduce, initialConfig);
  const activeSteps = config.order.filter((name) => config.enabled[name]);
  return { config, dispatch, activeSteps };
}
