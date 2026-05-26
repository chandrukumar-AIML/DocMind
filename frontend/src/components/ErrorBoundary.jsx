// frontend/src/components/ErrorBoundary.jsx
// DVMELTSS-FIX: E - Error handling, A - Accessibility, M - Modular
// ASCALE-FIX: S - Separation, L - Layered
import { Component } from "react";
import PropTypes from "prop-types";

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    // Log to monitoring service (e.g., Sentry, LangSmith)
    console.error("DocuMind render error:", error, errorInfo);
    
    // Optional: send to error tracking service
    // if (window.Sentry) {
    //   Sentry.captureException(error, { extra: { componentStack: errorInfo.componentStack } });
    // }
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div 
          className="flex flex-col items-center justify-center h-full gap-3 p-8 text-center"
          role="alert"
          aria-live="assertive"
        >
          <div className="w-12 h-12 rounded-full bg-red-100 dark:bg-red-900/30 flex items-center justify-center text-red-500 text-xl" aria-hidden="true">
            ⚠️
          </div>
          <p className="text-sm text-red-500 dark:text-red-400 font-medium">
            Something went wrong rendering this section.
          </p>
          <p className="text-xs text-gray-400 dark:text-gray-500 max-w-md">
            {this.state.error?.message || "Unknown error"}
          </p>
          <button
            onClick={this.handleRetry}
            className="text-xs text-blue-500 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500 rounded"
            aria-label="Try rendering again"
          >
            Try again
          </button>
          {import.meta.env?.DEV && this.state.errorInfo && (
            <details className="mt-4 text-left text-xs text-gray-400 max-w-lg">
              <summary className="cursor-pointer hover:text-gray-300">Error details (dev only)</summary>
              <pre className="mt-2 p-2 bg-gray-900 rounded overflow-auto max-h-40">
                {this.state.errorInfo.componentStack}
              </pre>
            </details>
          )}
        </div>
      );
    }

    return this.props.children;
  }
}

ErrorBoundary.propTypes = {
  children: PropTypes.node.isRequired,
};