// Small preview images for files the seller has chosen but not uploaded yet.
//
// Showing the original files directly meant dozens of full-size images for the
// browser to decode, and to decode again whenever it dropped them while
// scrolling: the grid went blank on a fast scroll. A ~320px copy decodes in no
// time and costs a fraction of the memory. The caller owns the returned object
// URL and revokes it when the preview goes away.

const UNPREVIEWABLE = /\.tiff?$/i; // a browser will not paint these

export async function smallPreview(file: File, width = 320): Promise<string | null> {
  if (UNPREVIEWABLE.test(file.name)) return null;
  try {
    // Height follows the aspect ratio when only the width is given.
    const bitmap = await createImageBitmap(file, { resizeWidth: width, resizeQuality: "medium" });
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    canvas.getContext("2d")?.drawImage(bitmap, 0, 0);
    bitmap.close();
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.82));
    return blob ? URL.createObjectURL(blob) : null;
  } catch {
    // Unusual formats createImageBitmap cannot read: the file itself still previews.
    return URL.createObjectURL(file);
  }
}
