"""Existing byte-pinned public model materials, shared by builder and recovery.
Private/fine-tuned models have no entry and cannot be downloaded by this catalog.
"""
TRUSTED_MODEL_REPOSITORY = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
TRUSTED_MODEL_REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"
TRUSTED_MODEL_FILES = {
    "config.json": (4494, "2e714c787c8edb98b05432685cddb634add2de4d4e645f653d68251ef72ba011"),
    "generation_config.json": (245, "f1b90b4513f3b34c62851049e2492d7b4c5940daf1276f89c82b8ef04127f3aa"),
    "merges.txt": (1671839, "599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3"),
    "model.safetensors": (1829344272, "180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6"),
    "preprocessor_config.json": (127, "efdde1022ea9d76928bf7a9cd53139138f5ba2e466e837f08f6105ab1af1c119"),
    "tokenizer_config.json": (7344, "dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670"),
    "vocab.json": (2776833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
    "speech_tokenizer/config.json": (2336, "ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167"),
    "speech_tokenizer/configuration.json": (76, "6bc26d64eb5024b4d1dab5a52371958b429256d6c9d59787f1f5294a54e0cebd"),
    "speech_tokenizer/model.safetensors": (682293092, "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"),
    "speech_tokenizer/preprocessor_config.json": (234, "fcb3805e597e786d4067706e602f6688524640f8d3396790e2e09b5942fcbdfb"),
}
TRUSTED_MODEL_TREE_SHA256 = "3b927a09324c8d56379011ca2b85b97c8a31db8661e1bb31664d5f62c6a72fac"


def material_for_hash(sha256):
    return next(((path, size, digest) for path,(size,digest) in TRUSTED_MODEL_FILES.items() if digest==sha256), None)

def material_url(path):
    if path not in TRUSTED_MODEL_FILES: raise ValueError('UNTRUSTED_MODEL_MATERIAL')
    return f'https://huggingface.co/{TRUSTED_MODEL_REPOSITORY}/resolve/{TRUSTED_MODEL_REVISION}/{path}'
