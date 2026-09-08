/** @jest-environment node */

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import RootLayout, { viewport } from "./layout";

test("layout loads locked fonts and dark chrome", () => {
  const html = renderToStaticMarkup(<RootLayout><div /></RootLayout>);

  expect(html).toContain("family=Space+Grotesk:wght@400;500;600;700");
  expect(html).toContain("family=JetBrains+Mono:wght@400;500;700");
  expect(viewport.themeColor).toBe("#06070b");
});
