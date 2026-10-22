import {getLanguage} from './i18n.js';
import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
import "./styles.css";
import {applyInitialTheme} from "./theme.js";

applyInitialTheme();
document.documentElement.lang=getLanguage();

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
