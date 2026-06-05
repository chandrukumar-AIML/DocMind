// Vitest global setup — extends expect with jest-dom matchers
import "@testing-library/jest-dom";

// jsdom does not implement scrollIntoView — stub it so components that
// auto-scroll (e.g. chat/agent panels) can render in tests.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
