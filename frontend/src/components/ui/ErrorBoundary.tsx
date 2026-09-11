import { Component, type ErrorInfo, type ReactNode } from "react";

interface State { error: Error | null }

/** Catches render errors so one broken panel does not blank the page. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };
  static getDerivedStateFromError(error: Error): State { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("Render error", error, info.componentStack); }
  render() {
    if (this.state.error) {
      return (
        <div className="rounded-md bg-error-bg px-3 py-2.5 text-sm text-error-ink" role="alert">
          <strong className="font-semibold">This panel failed to render.</strong> {this.state.error.message}{" "}
          <button className="btn-link" onClick={() => this.setState({ error: null })}>Try again</button>
        </div>
      );
    }
    return this.props.children;
  }
}
