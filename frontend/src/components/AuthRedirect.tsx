import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { UNAUTHORIZED_EVENT } from "../lib/errors";

/** Sends the user to the login page when the API says their session expired. */
export default function AuthRedirect() {
  const navigate = useNavigate();
  useEffect(() => {
    const onExpired = () => navigate("/login?expired=1");
    window.addEventListener(UNAUTHORIZED_EVENT, onExpired);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onExpired);
  }, [navigate]);
  return null;
}
