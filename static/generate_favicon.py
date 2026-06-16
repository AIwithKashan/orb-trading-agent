import zlib
import struct
from pathlib import Path

def make_png(width, height, pixels):
    raw_data = b""
    for y in range(height):
        raw_data += b"\x00" # Filter type 0
        for x in range(width):
            raw_data += pixels[y * width + x]
            
    compressed = zlib.compress(raw_data)
    
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
        
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", compressed)
    png += chunk(b"IEND", b"")
    return png

def main():
    width = 32
    height = 32
    pixels = []
    
    for y in range(height):
        for x in range(width):
            # Transparent background
            r, g, b, a = 0, 0, 0, 0
            
            # Draw a sleek circular background border for a premium app icon
            dist = ((x - 15.5) ** 2 + (y - 15.5) ** 2) ** 0.5
            if dist <= 15.5:
                # App base: dark blue slate gradient
                factor = (y / 31.0)
                r = int(15 + factor * 20)
                g = int(23 + factor * 30)
                b = int(42 + factor * 50)
                a = 255
                
                # Draw grid lines inside the circle
                if x % 8 == 0 or y % 8 == 0:
                    r, g, b = int(r * 1.3), int(g * 1.3), int(b * 1.3)
                
                # Draw green chart trend line: x goes 4 to 28
                if 4 <= x <= 28:
                    # upward trend with a dip and a rise
                    val = 22 - int((x - 4) * 0.6)
                    if x > 14:
                        val -= int((x - 14) * 0.4)
                    
                    if abs(y - val) < 1.0:
                        r, g, b, a = 16, 185, 129, 255 # Emerald line
                    elif abs(y - val) < 2.0:
                        r, g, b, a = 16, 185, 129, 150 # Glow
                    elif y > val:
                        # Area shading under the curve
                        r = int(r * 0.7 + 16 * 0.3)
                        g = int(g * 0.7 + 185 * 0.3)
                        b = int(b * 0.7 + 129 * 0.3)
            
            pixels.append(struct.pack("BBBB", r, g, b, a))

    png_bytes = make_png(width, height, pixels)
    
    target_path = Path("d:/Trading View Agenet/static/favicon.png")
    target_path.parent.mkdir(exist_ok=True)
    with open(target_path, "wb") as f:
        f.write(png_bytes)
        
    print(f"Beautiful custom favicon generated successfully at {target_path}")

if __name__ == "__main__":
    main()
