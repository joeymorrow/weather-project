import os

file_path = "/home/frigate/repos/weather-project/templates/index.html"

with open(file_path, "r") as f:
    content = f.read()

replacements = [
    ("#buddy-container:hover { z-index: 2500 !important; transform: scale(1) translate(0px, 0px) !important; filter: brightness(1) !important; transition: left 1.5s ease-in-out, top 1.5s ease-in-out, transform 0.3s ease, filter 0.3s ease, z-index 0s !important; }",
     "#buddy-container:hover { z-index: 2500 !important; transform: scale(1) !important; filter: brightness(1) !important; transition: left 1.5s ease-in-out, top 1.5s ease-in-out, transform 0.3s ease, filter 0.3s ease, z-index 0s !important; }"),
    (".bg-mode { transform: scale(0.85) translate(45px, -35px); z-index: 20 !important; filter: brightness(1); }",
     ".bg-mode { transform: scale(0.85); z-index: 3 !important; filter: brightness(0.85); }"),
    ("@keyframes orbit-front { 0% { transform: scale(0.75) translateX(0); z-index: 3; filter: brightness(0.85); } 49% { z-index: 3; } 50% { transform: scale(0.85) translateX(140px); z-index: 1000; filter: brightness(0.95); } 100% { transform: scale(1) translateX(0); z-index: 1000; filter: brightness(1); } }",
     "@keyframes orbit-front { 0% { transform: scale(0.85); z-index: 3; filter: brightness(0.85); } 49% { z-index: 3; } 50% { transform: scale(0.95) translateY(-15px); z-index: 1000; filter: brightness(0.95); } 100% { transform: scale(1); z-index: 1000; filter: brightness(1); } }"),
    ("@keyframes orbit-back { 0% { transform: scale(1) translateX(0); z-index: 1000; filter: brightness(1); } 49% { z-index: 1000; } 50% { transform: scale(0.85) translateX(-140px); z-index: 3; filter: brightness(0.95); } 100% { transform: scale(0.75) translateX(0); z-index: 3; filter: brightness(0.85); } }",
     "@keyframes orbit-back { 0% { transform: scale(1); z-index: 1000; filter: brightness(1); } 49% { z-index: 1000; } 50% { transform: scale(0.95) translateY(-15px); z-index: 3; filter: brightness(0.95); } 100% { transform: scale(0.85); z-index: 3; filter: brightness(0.85); } }")
]

for old, new in replacements:
    content = content.replace(old, new)

with open(file_path, "w") as f:
    f.write(content)

print("Successfully applied animation fixes to templates/index.html")