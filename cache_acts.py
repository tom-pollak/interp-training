# %%
import gc
import math
import torch as t
from sae_lens.cache_activations_runner import (
    CacheActivationsRunner,
    CacheActivationsRunnerConfig,
)
from sae_lens.config import DTYPE_MAP
from datasets import Dataset, concatenate_datasets
import multiprocessing as mp
from functools import partial

# %%
mp.set_start_method('spawn', force=True)

def run_single_step(step, base_config, gpu_id=0):
    """Run caching for a single step with specific GPU"""
    if base_config['device'] == 'cuda':
        t.cuda.set_device(gpu_id)
    revision = f"step{step}"
    cfg = CacheActivationsRunnerConfig(
        **base_config,
        new_cached_activations_path=f"{base_config.pop('activation_path')}/{revision}",
        model_from_pretrained_kwargs={"revision": revision},
    )
    runner = CacheActivationsRunner(cfg)
    runner.run()

    del runner
    gc.collect()
    if base_config['device'] == "cuda":
        t.cuda.empty_cache()
    elif base_config['device'] == "mps":
        t.mps.empty_cache()

def main():
    # Restore all the configuration from before
    device = "cuda" if t.cuda.is_available() else "mps" if t.mps.is_available() else "cpu"

    model_name = "EleutherAI/pythia-70m"
    hook_layer = 4
    dataset_path = "NeelNanda/pile-small-tokenized-2b"
    activation_path = f"activations/pythia-70m-layer-{hook_layer}-resid-post/"

    # Model and data config
    training_tokens = 10_000_000
    model_batch_size = 256
    n_batches_in_buffer = 28
    d_in = 512
    context_size = 128
    dtype = "float32"

    # Calculate memory requirements
    tokens_in_batch = model_batch_size * n_batches_in_buffer * context_size
    n_bytes_in_buffer = tokens_in_batch * d_in * DTYPE_MAP[dtype].itemsize
    n_buffers = math.ceil(training_tokens / tokens_in_batch)

    print(
        f"GB in buffer: {n_bytes_in_buffer / 1e9} | Num buffers: {n_buffers} | device: {device}"
    )

    steps = [
        256,
        1000,
        5000,
        10_000,
        50_000,
        100_000,
        143_000,
    ]

    # Base configuration dictionary
    base_config = {
        'model_name': model_name,
        'hook_name': f"blocks.{hook_layer}.hook_resid_post",
        'hook_layer': hook_layer,
        'context_size': context_size,
        'd_in': d_in,
        'device': str(device),
        'dataset_path': dataset_path,
        'is_dataset_tokenized': True,
        'prepend_bos': False,
        'shuffle': False,
        'seed': 42,
        'activation_path': activation_path,
        'act_store_device': "cpu",
        'store_batch_size_prompts': model_batch_size,
        'training_tokens': training_tokens,
        'n_batches_in_buffer': n_batches_in_buffer,
    }

    # Use one process per step
    n_processes = len(steps)
    n_gpus = t.cuda.device_count()
    print(f"Running {n_processes} processes across {n_gpus} GPUs")

    try:
        with mp.Pool(n_processes) as pool:
            pool.starmap(
                run_single_step,
                [(step, base_config, i % n_gpus) for i, step in enumerate(steps)]
            )
            print("All caching processes completed successfully")
    except Exception as e:
        print(f"Error during parallel processing: {e}")
        raise

    print("Starting dataset concatenation...")
    # After all processes complete, concatenate the datasets
    dss = []
    for step in steps:
        revision = f"step{step}"
        _ds = Dataset.load_from_disk(f"{activation_path}/{revision}")
        _ds = _ds.rename_column('blocks.4.hook_resid_post', str(step))
        dss.append(_ds)

    ds = concatenate_datasets(dss, axis=1)
    ds.push_to_hub(f"pythia-70m-layer-{hook_layer}-pile-resid-post-activations-through-time")

if __name__ == '__main__':
    main()
