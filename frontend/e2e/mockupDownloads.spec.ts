/**
 * On a phone (375 px): tick two mockups with a normal tap and download them (feature/mockups-editor).
 *
 * The checkboxes under the mockups once sat under their own label's 44 px tap overlay (.tap::after), so a tap was
 * taken by the overlay, not the checkbox. Each checkbox now has a 44 x 44 px box of its own. This test makes the
 * mockups the way a seller does (two drawn designs on the demo account's "Ten colours" template, made by the
 * worker), ticks two with an ordinary click, and downloads "Download selected" as a ZIP of exactly those two.
 * Synthetic demo data only; runs with the layout suite (LAYOUT_EMAIL / LAYOUT_PASSWORD), phone project only.
 */
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import sharp from "sharp";
import { signIn } from "./session";

const EMAIL = process.env.LAYOUT_EMAIL;
const PASSWORD = process.env.LAYOUT_PASSWORD;

async function drawn(word: string, colour: string) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1000"><circle cx="450" cy="420" r="320" fill="${colour}"/>
    <text x="450" y="470" font-family="Arial, sans-serif" font-size="160" font-weight="700" fill="#fff" text-anchor="middle">${word}</text></svg>`;
  return { name: `${word}.png`, mimeType: "image/png", buffer: await sharp(Buffer.from(svg)).png().toBuffer() };
}

/** The names in a ZIP's central directory. */
function zipNames(data: Buffer): string[] {
  const names: string[] = [];
  for (let i = data.indexOf("PK\x01\x02"); i >= 0; i = data.indexOf("PK\x01\x02", i + 4)) {
    const length = data.readUInt16LE(i + 28);
    names.push(data.subarray(i + 46, i + 46 + length).toString("utf8"));
  }
  return names;
}

test.describe("mockup downloads on a phone", () => {
  test.skip(!EMAIL || !PASSWORD, "set LAYOUT_EMAIL and LAYOUT_PASSWORD (the demo admin from `seed-demo --admin`) to run these");

  test("two mockups ticked with a normal tap, downloaded as a ZIP of those two", async ({ page }, info) => {
    test.skip(info.project.name !== "phone-375", "a phone's layout");
    test.setTimeout(240_000);
    await signIn(page, EMAIL!, PASSWORD!);
    await page.goto("/mockups");
    await page.getByTestId("mockup-templates").waitFor();
    const labels = await page.getByTestId("mockup-templates").locator("label").allInnerTexts();
    const ten = labels.findIndex((l) => l.includes("Ten colours"));
    expect(ten, "the demo account's Ten colours template").toBeGreaterThanOrEqual(0);
    await page.getByTestId("mockup-template").nth(ten).click();
    await page.getByTestId("mockup-designs-files").setInputFiles([await drawn("SUN", "#c2542d"), await drawn("SEA", "#2f5d8a")]);
    await expect(page.getByTestId("mockup-make")).toBeEnabled({ timeout: 120_000 });
    await page.getByTestId("mockup-make").click();
    await expect(page.getByTestId("mockup-zip")).toBeVisible({ timeout: 180_000 }); // the worker made them

    const boxes = page.getByTestId("mockup-pick-box");
    for (const n of [0, 1]) {
      const size = await boxes.nth(n).boundingBox();
      expect(size && size.width >= 44 && size.height >= 44, `the tap box is at least 44 px (${JSON.stringify(size)})`).toBe(true);
      await page.getByTestId("mockup-pick").nth(n).click(); // an ordinary tap: nothing may cover it
      await expect(page.getByTestId("mockup-pick").nth(n)).toBeChecked();
    }
    await expect(page.getByTestId("mockup-zip-selected")).toHaveText("Download selected (2)");
    await page.getByTestId("mockup-zip-selected").click();
    const link = page.getByTestId("mockup-zip-download");
    await expect(link).toBeVisible({ timeout: 120_000 });
    const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
    expect(download.suggestedFilename()).toMatch(/^mockups-\d{4}-\d{2}-\d{2}\.zip$/);
    const names = zipNames(readFileSync(await download.path()));
    expect(names.length, names.join(", ")).toBe(2);
    expect(names.every((n) => /\.jpg$/.test(n))).toBe(true);
  });
});
