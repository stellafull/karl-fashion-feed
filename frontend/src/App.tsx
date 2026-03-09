import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/NotFound";
import { Route, Router, Switch } from "wouter";
import ErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider } from "./contexts/ThemeContext";
import Home from "./pages/Home";

const routerBase =
  import.meta.env.BASE_URL === "/"
    ? ""
    : import.meta.env.BASE_URL.replace(/\/$/, "");

function AppRoutes() {
  return (
    <Switch>
      <Route path={"/"} component={Home} />
      <Route path={"/404"} component={NotFound} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <Router base={routerBase}>
        <ThemeProvider defaultTheme="light">
          <TooltipProvider>
            <Toaster />
            <AppRoutes />
          </TooltipProvider>
        </ThemeProvider>
      </Router>
    </ErrorBoundary>
  );
}

export default App;
