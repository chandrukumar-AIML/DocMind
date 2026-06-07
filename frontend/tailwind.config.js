/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  // Drive Tailwind's `dark:` variant from the app's own theme class (set on <html>
  // by App.jsx) instead of the OS `prefers-color-scheme`. This keeps the Tailwind
  // dashboards (Monitoring, Graph, RAGAs, Versions…) visually in sync with the rest
  // of the Nebula-dark UI regardless of the user's OS theme.
  darkMode: "class",
  theme: {
    extend: {},
  },
  plugins: [],
}

