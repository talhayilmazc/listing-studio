// Written by scripts/hero-images.mjs; do not edit by hand.
// The home page's shirt pictures: the mockup engine's output on real blank-shirt photos
// (backend/scripts/hero_assets.py; every photo's source, photographer and licence is in docs/marketing-assets.md).
// Each picture's files at 1x and 2x (shop covers also 4x), AVIF and WebP, and its size in CSS px.
export type Shot = { avif: string; avif2x: string; webp: string; webp2x: string; width: number; height: number };
export type WideShot = Shot & { avif4x: string; webp4x: string };

export const SHOTS = {
  colour_black_mountain: { avif: "/hero/colour-black-mountain.avif", webp: "/hero/colour-black-mountain.webp", avif2x: "/hero/colour-black-mountain-2x.avif", webp2x: "/hero/colour-black-mountain-2x.webp", width: 96, height: 104 },
  colour_mint_mountain: { avif: "/hero/colour-mint-mountain.avif", webp: "/hero/colour-mint-mountain.webp", avif2x: "/hero/colour-mint-mountain-2x.avif", webp2x: "/hero/colour-mint-mountain-2x.webp", width: 96, height: 104 },
  colour_navy_mountain: { avif: "/hero/colour-navy-mountain.avif", webp: "/hero/colour-navy-mountain.webp", avif2x: "/hero/colour-navy-mountain-2x.avif", webp2x: "/hero/colour-navy-mountain-2x.webp", width: 96, height: 104 },
  colour_orange_mountain: { avif: "/hero/colour-orange-mountain.avif", webp: "/hero/colour-orange-mountain.webp", avif2x: "/hero/colour-orange-mountain-2x.avif", webp2x: "/hero/colour-orange-mountain-2x.webp", width: 96, height: 104 },
  colour_pink_mountain: { avif: "/hero/colour-pink-mountain.avif", webp: "/hero/colour-pink-mountain.webp", avif2x: "/hero/colour-pink-mountain-2x.avif", webp2x: "/hero/colour-pink-mountain-2x.webp", width: 96, height: 104 },
  colour_white_flat_mountain: { avif: "/hero/colour-white-flat-mountain.avif", webp: "/hero/colour-white-flat-mountain.webp", avif2x: "/hero/colour-white-flat-mountain-2x.avif", webp2x: "/hero/colour-white-flat-mountain-2x.webp", width: 96, height: 104 },
  shop_mint_mountain: { avif: "/hero/shop-mint-mountain.avif", webp: "/hero/shop-mint-mountain.webp", avif2x: "/hero/shop-mint-mountain-2x.avif", webp2x: "/hero/shop-mint-mountain-2x.webp", avif4x: "/hero/shop-mint-mountain-4x.avif", webp4x: "/hero/shop-mint-mountain-4x.webp", width: 300, height: 276 },
  shop_navy_mountain: { avif: "/hero/shop-navy-mountain.avif", webp: "/hero/shop-navy-mountain.webp", avif2x: "/hero/shop-navy-mountain-2x.avif", webp2x: "/hero/shop-navy-mountain-2x.webp", avif4x: "/hero/shop-navy-mountain-4x.avif", webp4x: "/hero/shop-navy-mountain-4x.webp", width: 300, height: 276 },
  shop_white_flat_mountain: { avif: "/hero/shop-white-flat-mountain.avif", webp: "/hero/shop-white-flat-mountain.webp", avif2x: "/hero/shop-white-flat-mountain-2x.avif", webp2x: "/hero/shop-white-flat-mountain-2x.webp", avif4x: "/hero/shop-white-flat-mountain-4x.avif", webp4x: "/hero/shop-white-flat-mountain-4x.webp", width: 300, height: 276 },
  tile_black_mountain: { avif: "/hero/tile-black-mountain.avif", webp: "/hero/tile-black-mountain.webp", avif2x: "/hero/tile-black-mountain-2x.avif", webp2x: "/hero/tile-black-mountain-2x.webp", width: 128, height: 132 },
  tile_black_paw: { avif: "/hero/tile-black-paw.avif", webp: "/hero/tile-black-paw.webp", avif2x: "/hero/tile-black-paw-2x.avif", webp2x: "/hero/tile-black-paw-2x.webp", width: 128, height: 132 },
  tile_mint_leaf: { avif: "/hero/tile-mint-leaf.avif", webp: "/hero/tile-mint-leaf.webp", avif2x: "/hero/tile-mint-leaf-2x.avif", webp2x: "/hero/tile-mint-leaf-2x.webp", width: 128, height: 132 },
  tile_navy_books: { avif: "/hero/tile-navy-books.avif", webp: "/hero/tile-navy-books.webp", avif2x: "/hero/tile-navy-books-2x.avif", webp2x: "/hero/tile-navy-books-2x.webp", width: 128, height: 132 },
  tile_orange_cup: { avif: "/hero/tile-orange-cup.avif", webp: "/hero/tile-orange-cup.webp", avif2x: "/hero/tile-orange-cup-2x.avif", webp2x: "/hero/tile-orange-cup-2x.webp", width: 128, height: 132 },
  tile_pink_heart: { avif: "/hero/tile-pink-heart.avif", webp: "/hero/tile-pink-heart.webp", avif2x: "/hero/tile-pink-heart-2x.avif", webp2x: "/hero/tile-pink-heart-2x.webp", width: 128, height: 132 },
  tile_white_flat_mountain: { avif: "/hero/tile-white-flat-mountain.avif", webp: "/hero/tile-white-flat-mountain.webp", avif2x: "/hero/tile-white-flat-mountain-2x.avif", webp2x: "/hero/tile-white-flat-mountain-2x.webp", width: 128, height: 132 },
  tile_white_rack_cup: { avif: "/hero/tile-white-rack-cup.avif", webp: "/hero/tile-white-rack-cup.webp", avif2x: "/hero/tile-white-rack-cup-2x.avif", webp2x: "/hero/tile-white-rack-cup-2x.webp", width: 128, height: 132 },
};
