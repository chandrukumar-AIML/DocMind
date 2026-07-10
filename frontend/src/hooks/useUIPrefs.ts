import { useState, useEffect } from "react";

type Theme = "dark" | "light";

interface UseUIPrefsReturn {
  theme: Theme;
  toggleTheme: () => void;
  visionEnabled: boolean;
  setVisionEnabled: (v: boolean) => void;
  sidebarOpen: boolean;
  setSidebarOpen: (v: boolean) => void;
  toggleSidebar: () => void;
}

export function useUIPrefs(): UseUIPrefsReturn {
  const [theme, setTheme] = useState<Theme>(() => {
    try { return (localStorage.getItem("dm_theme") as Theme) || "dark"; } catch { return "dark"; }
  });

  const [visionEnabled, setVisionEnabled] = useState<boolean>(() => {
    try { return localStorage.getItem("dm_vision") === "true"; } catch { return false; }
  });

  const [sidebarOpen, setSidebarOpen] = useState<boolean>(() => {
    try { return window.innerWidth > 900; } catch { return true; }
  });

  useEffect(() => {
    try { localStorage.setItem("dm_vision", String(visionEnabled)); } catch { /* storage unavailable */ }
  }, [visionEnabled]);

  useEffect(() => {
    try {
      localStorage.setItem("dm_theme", theme);
      document.documentElement.className = theme === "light" ? "theme-light" : "dark";
    } catch { /* storage unavailable */ }
  }, [theme]);

  useEffect(() => {
    const onResize = () => { if (window.innerWidth < 768) setSidebarOpen(false); };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const toggleTheme   = () => setTheme(t => t === "dark" ? "light" : "dark");
  const toggleSidebar = () => setSidebarOpen(v => !v);

  return { theme, toggleTheme, visionEnabled, setVisionEnabled, sidebarOpen, setSidebarOpen, toggleSidebar };
}
