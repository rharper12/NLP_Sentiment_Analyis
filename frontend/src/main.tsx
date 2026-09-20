import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { OperatorAccess } from "./components/OperatorAccess";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <OperatorAccess><App /></OperatorAccess>
  </StrictMode>,
);
