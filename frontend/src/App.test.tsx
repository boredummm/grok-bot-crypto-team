import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

it("shows the paper-trading gate before a token is stored", () => {
  sessionStorage.clear();
  render(
    <MemoryRouter>
      <App />
    </MemoryRouter>,
  );
  expect(screen.getByText("SOL SWARM")).toBeTruthy();
  expect(screen.getByText(/paper trading only/i)).toBeTruthy();
});
