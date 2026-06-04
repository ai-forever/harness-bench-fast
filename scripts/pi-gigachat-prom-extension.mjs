import { GIGACHAT_MODELS } from "/opt/homebrew/lib/node_modules/@gigachain/pi-gigachat/dist/models.js";
import { streamSimpleGigaChat } from "/opt/homebrew/lib/node_modules/@gigachain/pi-gigachat/dist/stream.js";

const GIGACHAT_API = "gigachat-extension-api";
const PROM_MODELS = [
  ...GIGACHAT_MODELS,
  {
    id: "GigaChat-3-Ultra",
    name: "GigaChat 3 Ultra",
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128000,
    maxTokens: 8192,
  },
];

function streamWithPasswordAuth(model, context, options = {}) {
  const { apiKey: _apiKey, ...rest } = options;
  return streamSimpleGigaChat(model, context, rest);
}

export default function registerPiGigaChatProm(pi) {
  pi.registerProvider("gigachat", {
    baseUrl: process.env.GIGACHAT_BASE_URL || "https://gigachat.sberdevices.ru/v1",
    apiKey: "__PI_GIGACHAT_PROM_IGNORED_API_KEY",
    api: GIGACHAT_API,
    models: PROM_MODELS,
    streamSimple: streamWithPasswordAuth,
  });
}
