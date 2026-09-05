import { useContext } from "react";
import { SkyGuardContext } from "../context/SkyGuardContext";

export function useSkyGuard() {
  const context = useContext(SkyGuardContext);
  if (!context) throw new Error("useSkyGuard must be used inside SkyGuardProvider");
  return context;
}
