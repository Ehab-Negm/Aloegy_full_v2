import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";

document.documentElement.setAttribute("lang", "ar");
document.documentElement.setAttribute("dir", "rtl");

createRoot(document.getElementById("root")!).render(<App />);
