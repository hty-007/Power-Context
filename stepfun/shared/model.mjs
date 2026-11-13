export const MODEL = {
  id: 'Xenova/bge-small-zh-v1.5',
  revision: '7a21cfa835a0d8cba006e36210e7843b232a1107',
  dimension: 512,
  queryPrefix: '为这个句子生成表示以用于检索相关文章：',
  weights: {
    fp32: 'onnx/model.onnx',
    q8: 'onnx/model_quantized.onnx',
  },
};

export const ASSETS = ['config.json', 'tokenizer.json', 'tokenizer_config.json', ...Object.values(MODEL.weights)];

export const ASSET_SHA256 = {
  'config.json': 'd4193ead3a810fd694fa8a31d7fc72fbaebc0668b603e398734bf2f6538ff42f',
  'tokenizer.json': '48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26',
  'tokenizer_config.json': 'e6f3b96db926a37d4039995fbf5ad17de158dfb8f6343d607e4dbaad18d75f5a',
  'onnx/model.onnx': '69a0b846f4f116b5e6aabf9546ea6754d02264f3211a13a1bd69b31b8040749a',
  'onnx/model_quantized.onnx': '15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc',
};
