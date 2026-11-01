import { expect, test, type Page } from "@playwright/test";
import { IDEAS_PATH } from "../lib/marketing/routes.mjs";

/**
 * The free tools in a real browser. The public pages' metadata, layout and headers are checked with every
 * other public page (marketing, layout and headers specs); here is what only a browser can show.
 *
 * Two builds: by default the switches are off, as deployed. With TOOLS_ON=1, against an image built with
 * FEE_CALCULATOR_CONFIRMED=true and run with FREE_TOOL_AI_ENABLED=true, the calculator and the ideas page
 * are tested. Cloudflare's script and our API are stubbed in the browser: nothing leaves this machine.
 */

const ON = process.env.TOOLS_ON === "1";
const SHOTS = process.env.TOOL_SHOTS ? "../load-output/tool-shots" : null;

async function shot(page: Page, name: string, project: string) {
  if (SHOTS && project !== "tablet-768") await page.screenshot({ path: `${SHOTS}/${name}-${project}.png`, fullPage: true });
}

test("the title checker checks as you type, offline, and says whose rule each line is", async ({ page }, info) => {
  const sent: string[] = [];
  await page.goto("/free-tools/title-checker");
  await page.waitForLoadState("networkidle");
  page.on("request", (r) => { sent.push(r.url()); });
  await expect(page.getByTestId("rule-length")).toHaveAttribute("data-verdict", "idle");
  await expect(page.getByTestId("rule-length")).toContainText("A listing's title can be up to 140 characters long.");
  await expect(page.getByTestId("rule-length").getByRole("link")).toHaveAttribute("href", /help\.etsy\.com/);
  await expect(page.getByTestId("rule-subjective").getByTestId("rule-kind")).toHaveText("Our advice");
  await expect(page.getByTestId("rule-length").getByTestId("rule-kind")).toHaveText("Etsy's rule");

  await page.getByTestId("title-input").fill("Frog Shirt");
  await expect(page.getByTestId("rule-length")).toHaveAttribute("data-verdict", "pass");
  await expect(page.getByTestId("brand-warning")).toHaveAttribute("data-brands", "0");

  await page.getByTestId("title-input").fill(`Disney ${"x".repeat(140)}`);
  await expect(page.getByTestId("rule-length")).toHaveAttribute("data-verdict", "fail");
  await expect(page.getByTestId("tool-summary")).toContainText("to fix");
  await expect(page.getByTestId("brand-warning")).toContainText("Disney is a trademark");
  await expect(page.getByTestId("brand-warning")).toContainText("Repeat reports of infringement");

  await page.getByTestId("title-example").click();
  await expect(page.getByTestId("rule-subjective")).toHaveAttribute("data-verdict", "warn");
  await expect(page.getByTestId("title-count")).toContainText("of 140 characters");
  await shot(page, "title-checker", info.project.name);
  expect(sent, "what you type stays in the browser").toEqual([]);
  expect(await page.context().cookies()).toEqual([]);
});

test("the tag checker counts, marks long tags and warns about brands, offline", async ({ page }, info) => {
  const sent: string[] = [];
  await page.goto("/free-tools/tag-checker");
  await page.waitForLoadState("networkidle");
  page.on("request", (r) => { sent.push(r.url()); });
  await page.getByTestId("tags-example").click();
  await expect(page.getByTestId("tags-count")).toHaveText("7 of 13 tags");
  await expect(page.getByTestId("rule-length")).toHaveAttribute("data-verdict", "fail");
  await expect(page.getByTestId("rule-characters")).toContainText('write "and"');
  await expect(page.getByTestId("rule-duplicates")).toHaveAttribute("data-verdict", "warn");
  await expect(page.getByTestId("rule-title_words")).toHaveAttribute("data-verdict", "warn");
  await expect(page.getByTestId("tag-list").locator("li")).toHaveCount(7);
  await shot(page, "tag-checker", info.project.name);
  await page.getByTestId("tags-input").fill("mick ey tee\nplain tag");
  await expect(page.getByTestId("brand-warning")).toContainText("Mickey is a trademark");
  expect(sent).toEqual([]);
  expect(await page.context().cookies()).toEqual([]);
});

test("the free tools page leads to each tool, and a tool leads to its guide and the invitation", async ({ page }, info) => {
  await page.goto("/free-tools");
  await expect(page.getByTestId("free-tool")).toHaveCount(ON ? 4 : 3);
  await expect(page.getByRole("link", { name: "Title checker" })).toHaveAttribute("href", "/free-tools/title-checker");
  await expect(page.getByTestId("tool-status")).toHaveCount(ON ? 0 : 1);
  await shot(page, "free-tools", info.project.name);
  await page.goto("/free-tools/title-checker");
  await expect(page.getByTestId("tool-guide")).toHaveAttribute("href", "/guides/etsy-title-rules-2026");
  await expect(page.getByTestId("tool-cta").getByRole("link")).toHaveText("Do this for 100 listings at once with Listyro");
  await expect(page.getByTestId("tool-cta").getByRole("link")).toHaveAttribute("href", "/request-invite");
});

test("the fee calculator: coming soon, with no figure on the page", async ({ page, request }, info) => {
  test.skip(ON, "this build has the calculator on");
  await page.goto("/free-tools/fee-calculator");
  await expect(page.getByTestId("fee-coming-soon")).toContainText("Coming soon");
  await expect(page.getByTestId("fee-result")).toHaveCount(0);
  await expect(page.getByRole("main")).not.toContainText("Enter your price");
  await shot(page, "fee-calculator-coming-soon", info.project.name);
  const html = await (await request.get("/free-tools/fee-calculator")).text();
  for (const figure of ["6.5%", "0.20 USD", "$0.20", "1.97"]) expect(html, figure).not.toContain(figure);
});

test("the ideas page does not exist while its flag is off, and nothing about it is public", async ({ request }) => {
  test.skip(ON, "this build has the page on");
  const page = await request.get(IDEAS_PATH);
  expect(page.status()).toBe(404);
  expect(page.headers()["cache-control"]).toMatch(/^private/);
  expect(await (await request.get("/sitemap.xml")).text()).not.toContain(IDEAS_PATH);
  expect(await (await request.get("/free-tools")).text()).not.toContain("listing-ideas");
});

test("the fee calculator works it out, line by line", async ({ page }, info) => {
  test.skip(!ON, "needs a build with FEE_CALCULATOR_CONFIRMED=true");
  const sent: string[] = [];
  await page.goto("/free-tools/fee-calculator");
  await page.waitForLoadState("networkidle");
  page.on("request", (r) => { sent.push(r.url()); });
  await expect(page.getByTestId("fee-needs")).toContainText("Enter your item's price");
  await expect(page.getByTestId("fee-profit")).toHaveCount(0); // blank, not zero
  await page.getByTestId("fee-price").fill("25");
  await page.getByTestId("fee-shipping-charged").fill("5");
  await page.getByTestId("fee-base").fill("12");
  await page.getByTestId("fee-shipping-cost").fill("4.50");
  await expect(page.getByTestId("fee-profit")).toHaveText("10.20 USD");
  await expect(page.getByTestId("fee-margin")).toHaveText("34%");
  await expect(page.getByTestId("fee-total")).toHaveText("3.30 USD");
  await page.getByTestId("fee-offsite").selectOption("under");
  await expect(page.getByTestId("fee-line-offsite")).toContainText("-4.50 USD");
  await expect(page.getByTestId("fees-checked")).toContainText("Fees checked on 1 November 2026. Etsy can change its fees");
  await expect(page.getByTestId("fee-source-listing")).toContainText("To confirm");
  await shot(page, "fee-calculator", info.project.name);
  await page.getByTestId("fee-place").selectOption("United Kingdom");
  await expect(page.getByTestId("fee-needs")).toContainText("1 USD is in GBP");
  await page.getByTestId("fee-rate").fill("0.8");
  await expect(page.getByTestId("fee-line-regulatory")).toContainText("-0.14 GBP");
  expect(sent).toEqual([]);
  expect(await page.context().cookies()).toEqual([]);
});

test("the ideas page: private, checked by Turnstile, and it says when it runs out", async ({ page, request }, info) => {
  test.skip(!ON, "needs FREE_TOOL_AI_ENABLED=true");
  const direct = await request.get(IDEAS_PATH);
  expect(direct.status()).toBe(200);
  expect(direct.headers()["cache-control"]).toMatch(/^private/);
  expect(direct.headers()["cache-control"]).not.toMatch(/public|s-maxage/);
  expect(direct.headers()["set-cookie"]).toBeUndefined();
  expect(direct.headers()["content-security-policy"]).toContain("https://challenges.cloudflare.com");
  expect((await request.get("/free-tools/title-checker")).headers()["content-security-policy"]).not.toContain("challenges.cloudflare.com");
  expect(await (await request.get("/sitemap.xml")).text()).toContain(IDEAS_PATH);
  // Rendered per request, and its metadata is still in the head: the title rule holds there too.
  const head = (await direct.text()).split("</head>")[0];
  expect(head).toContain('<meta name="description"');
  expect(head).toContain('rel="canonical"');
  expect(head).toContain("<title>Listing title and tag ideas from a description | Listyro</title>");
  expect(head).toContain('property="og:title"');

  const bodies: { description: string; turnstile_token: string }[] = [];
  await page.route("https://challenges.cloudflare.com/**", (route) => route.fulfill({
    contentType: "text/javascript",
    body: "window.turnstile={render:(el,o)=>{el.textContent='checked';setTimeout(()=>o.callback('stub-token'),0);return 'w'},reset:()=>{}};",
  }));
  await page.route("**/api/free-tools/ideas", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: { available: true, reason: null, message: null, site_key: "stub", remaining: 3, per_day: 3 } });
    bodies.push(route.request().postDataJSON());
    return route.fulfill({ json: { titles: ["Ceramic Frog Mug, Green Glaze"], tags: ["pottery cup", "forest kitchen"], remaining: 2 } });
  });
  await page.goto(IDEAS_PATH);
  await expect(page.getByTestId("ideas-remaining")).toHaveText("3 of 3 left today");
  await expect(page.getByTestId("ideas-check")).toHaveText("checked");
  await expect(page.getByTestId("ideas-submit")).toBeDisabled(); // nothing to send yet
  await page.getByTestId("ideas-input").fill("A green ceramic mug with a frog painted on it");
  await expect(page.getByTestId("ideas-submit")).toBeEnabled();
  await page.getByTestId("ideas-submit").click();
  await expect(page.getByTestId("ideas-result")).toContainText("Ceramic Frog Mug");
  await expect(page.getByTestId("ideas-remaining")).toHaveText("2 of 3 left today");
  expect(bodies[0]).toEqual({ description: "A green ceramic mug with a frog painted on it", turnstile_token: "stub-token" });
  await shot(page, "listing-ideas", info.project.name);

  await page.unroute("**/api/free-tools/ideas");
  await page.route("**/api/free-tools/ideas", (route) => route.fulfill({ json: { available: false, reason: "spent", message: "Today's free ideas have run out. They come back after 00:00 UTC.", site_key: null, remaining: 0, per_day: 0 } }));
  await page.reload();
  await expect(page.getByTestId("ideas-message")).toContainText("have run out");
  await expect(page.getByTestId("ideas-submit")).toBeDisabled();
});
