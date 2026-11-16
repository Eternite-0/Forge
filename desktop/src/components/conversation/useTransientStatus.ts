import { useEffect, useRef, useState } from "react";

export default function useTransientStatus() {
  const [status, setStatus] = useState("");
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const show = (message: string) => {
    window.clearTimeout(timer.current);
    setStatus(message);
    timer.current = window.setTimeout(() => setStatus(""), 2000);
  };
  return [status, show] as const;
}

