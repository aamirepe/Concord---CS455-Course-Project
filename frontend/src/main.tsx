import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import LoginPage from "./pages/LoginPage";
import SignupPage from "./pages/SignupPage";
import DashboardPage from "./pages/DashboardPage";
import SessionPage from "./pages/SessionPage";
import MetricsPage from "./pages/MetricsPage";
import Toasts from "./components/Toasts";
import AuthRedirect from "./components/AuthRedirect";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthRedirect />
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/projects/:projectId" element={<SessionPage />} />
        <Route path="/metrics" element={<MetricsPage />} />
      </Routes>
      <Toasts />
    </BrowserRouter>
  </React.StrictMode>
);
