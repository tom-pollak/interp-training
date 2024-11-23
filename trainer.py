import json
from crosscoder import CrossCoder, CrossCoderConfig
from buffer import MultiFeatureBuffer
from tqdm import tqdm
import torch as t
import wandb
from dataclasses import asdict, dataclass


@dataclass
class TrainerConfig:
    # Training
    batch_size: int
    # Optimizer
    lr: float
    beta1: float
    beta2: float
    l1_coeff: float
    # Dataset
    dataset_repo_id: str
    # Logging
    wandb_project: str
    wandb_entity: str
    log_every: int
    save_every: int
    dump_dir: str


class Trainer:
    def __init__(self, trainer_cfg: TrainerConfig, crosscoder_cfg: CrossCoderConfig):
        self.cfg = trainer_cfg
        self.crosscoder = CrossCoder(crosscoder_cfg)
        self.buffer = MultiFeatureBuffer(self.cfg.dataset_repo_id)

    def lr_lambda(self, step):
        if step < 0.8 * self.total_steps:
            return 1.0
        else:
            return 1.0 - (step - 0.8 * self.total_steps) / (0.2 * self.total_steps)

    def get_l1_coeff(self):
        # Linearly increases from 0 to cfg["l1_coeff"] over the first 0.05 * self.total_steps steps, then keeps it constant
        if self.step_counter < 0.05 * self.total_steps:
            return self.cfg.l1_coeff * self.step_counter / (0.05 * self.total_steps)
        else:
            return self.cfg.l1_coeff

    def step(self, batch):
        losses = self.crosscoder.get_losses(batch)
        loss = losses.l2_loss + self.get_l1_coeff() * losses.l1_loss
        loss.backward()
        t.nn.utils.clip_grad_norm_(self.crosscoder.parameters(), max_norm=1.0)
        self.optimizer.step()
        self.scheduler.step()
        self.optimizer.zero_grad()

        loss_dict = {
            "loss": loss.item(),
            "l2_loss": losses.l2_loss.item(),
            "l1_loss": losses.l1_loss.item(),
            "l0_loss": losses.l0_loss.item(),
            "l1_coeff": self.get_l1_coeff(),
            "lr": self.scheduler.get_last_lr()[0],
            "explained_variance": losses.explained_variance.mean().item(),
            **{
                f"explained_variance_{i}": losses.explained_variance_per_model[i]
                .mean()
                .item()
                for i in range(losses.explained_variance_per_model.shape[0])
            },
        }
        self.step_counter += 1
        return loss_dict

    def log(self, loss_dict):
        wandb.log(loss_dict, step=self.step_counter)
        if self.step_counter % self.cfg.log_every == 0:
            tqdm.write(str(loss_dict))

    def save(self):
        self.crosscoder.save(self.cfg.dump_dir)
        with open(f"{self.cfg.dump_dir}/trainer_cfg.json", "w") as f:
            json.dump(asdict(self.cfg), f)

    def train(self):
        wandb.init(project=self.cfg.wandb_project, entity=self.cfg.wandb_entity)
        self.dl = self.buffer.dl(batch_size=self.cfg.batch_size)
        self.total_steps = len(self.dl)
        self.optimizer = t.optim.Adam(
            self.crosscoder.parameters(),
            lr=self.cfg.lr,
            betas=(self.cfg.beta1, self.cfg.beta2),
        )
        self.scheduler = t.optim.lr_scheduler.LambdaLR(self.optimizer, self.lr_lambda)
        self.step_counter = 0
        try:
            for i, batch in enumerate(tqdm(self.dl)):
                loss_dict = self.step(batch)
                self.log(loss_dict)
                if (i + 1) % self.cfg.save_every == 0:
                    self.save()
        finally:
            self.save()
