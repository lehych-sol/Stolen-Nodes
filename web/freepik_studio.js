import { app } from "../../scripts/app.js";

const TARGET_NODE = "DarkHubFreepikStudio";

app.registerExtension({
  name: "darkhub.seedream45.clean",
  nodeCreated(node) {
    if (node?.comfyClass !== TARGET_NODE && node?.type !== TARGET_NODE) return;
    node.title = "darkHUB Seedream 4.5";
    node.color = "#2f5d46";
    node.bgcolor = "#102f22";
  },
});
