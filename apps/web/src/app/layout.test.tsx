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

test("layout gives screens a full-height dark body to fill", () => {
  const html = renderToStaticMarkup(<RootLayout><div /></RootLayout>);

  expect(html).toContain("html,body{height:100%;margin:0;background:#06070b;overflow:hidden;overscroll-behavior:none}");
  // iOS still scrolls the document under an overflow-hidden body (seen in
  // the home-screen app); pinning the body leaves nothing to scroll.
  expect(html).toContain("body{position:fixed;inset:0;display:flex;flex-direction:column}");
});
