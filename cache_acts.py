# %%
import math
import torch as t
from sae_lens.cache_activations_runner import (
    CacheActivationsRunner,
    CacheActivationsRunnerConfig,
)
from sae_lens.config import DTYPE_MAP
import shutil

# %%

device = "cuda" if t.cuda.is_available() else "mps" if t.mps.is_available() else "cpu"

steps = [
    # 256,
    512,
    1000,
    5000,
    10_000,
    50_000,
    75_000,
    100_000,
    125_000,
    143_000,
]

model_name = "EleutherAI/pythia-70m"
hook_layer = 4

dataset_path = "NeelNanda/pile-small-tokenized-2b"

training_tokens = 10_000_000
model_batch_size = 256
n_batches_in_buffer = 25

d_in = 512
context_size = 128

dtype = "float32"

tokens_in_batch = model_batch_size * n_batches_in_buffer * context_size
n_bytes_in_buffer = tokens_in_batch * d_in * DTYPE_MAP[dtype].itemsize
n_buffers = math.ceil(training_tokens / tokens_in_batch)

print(
    f"GB in buffer: {n_bytes_in_buffer / 1e9} | Num buffers: {n_buffers} | device: {device}"
)

# %%

for step in steps:
    revision = f"step{step}"
    cfg = CacheActivationsRunnerConfig(
        ## Model
        model_name=model_name,
        hook_name=f"blocks.{hook_layer}.hook_resid_post",
        hook_layer=hook_layer,
        context_size=context_size,
        d_in=d_in,
        device=str(device),
        ## Dataset
        dataset_path=dataset_path,
        is_dataset_tokenized=True,
        prepend_bos=True,
        shuffle=False,
        seed=42,
        ## Activation
        new_cached_activations_path=f"activations/pythia-70m-layer-{hook_layer}-resid-post/{revision}/",
        act_store_device="cpu",
        hf_repo_id=f"pythia-70m-layer-{hook_layer}-pile-resid-post-activations",
        hf_revision=revision,
        ### Cache config
        store_batch_size_prompts=model_batch_size,
        training_tokens=training_tokens,
        n_batches_in_buffer=n_batches_in_buffer,
        model_from_pretrained_kwargs={"revision": revision},
    )
    runner = CacheActivationsRunner(cfg)
    runner.run()
    shutil.rmtree(cfg.new_cached_activations_path)
