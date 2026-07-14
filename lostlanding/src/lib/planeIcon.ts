/**
 * The live map's aircraft marker: an actual plane silhouette (nose pointing
 * up/north at rotation 0), not a dot or circle. Styled to the site's Art Deco
 * palette -- gold fill so it reads against the dark map, with a dark outline
 * so it stays legible over any basemap tile.
 */
export const PLANE_ICON_GOLD = "#d9a441";
export const PLANE_ICON_OUTLINE = "#061c25";

/** A simple top-down aircraft silhouette, nose at the top (0deg = north). */
export function planeIconSvg(size = 28): string {
  return `
    <svg width="${size}" height="${size}" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path
        d="M12 1.2 L14 8.4 L22.5 13.2 L22.5 15.6 L14 13.2 L14 18.8 L17.6 21.4 L17.6 23.2 L12 21.8 L6.4 23.2 L6.4 21.4 L10 18.8 L10 13.2 L1.5 15.6 L1.5 13.2 L10 8.4 Z"
        fill="${PLANE_ICON_GOLD}"
        stroke="${PLANE_ICON_OUTLINE}"
        stroke-width="1.1"
        stroke-linejoin="round"
      />
    </svg>
  `.trim();
}
