import { useCallback, useEffect, useState } from "react";
import { View } from "../workspaceTypes";
import type { SettingId } from "../SettingsView";

/** State retained independently of mounted workspace panels. */
export default function useWorkspaceNavigation() {
  const [view, setView] = useState<View>("chat");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [setting, setSetting] = useState<SettingId>("general");
  const openSettings = useCallback(() => {
    setSetting("general"); setSettingsOpen(true); setView("settings");
    requestAnimationFrame(() => document.querySelector<HTMLInputElement>('.settings-search input')?.focus());
  }, []);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key === ",") { event.preventDefault(); openSettings(); }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [openSettings]);
  return { view, setView, settingsOpen, setSettingsOpen, sidebarCollapsed, setSidebarCollapsed, setting, setSetting, openSettings };
}

