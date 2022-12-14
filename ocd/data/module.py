import lightning
import typing as th
import torch
from torch.utils.data import random_split, DataLoader, ConcatDataset
import dycode

from ocd.data.scm import SCMGenerator
from .dataset import OCDDataset
import numpy as np


class OCDDataModule(lightning.LightningDataModule):
    def __init__(
        self,
        # dataset (train and val)
        observation_size: int,
        # local interventional episodes
        interventional_episode_count: th.Optional[int] = None,
        interventional_episode_size: th.Optional[int] = None,
        intervention_function: th.Optional[th.Union[th.Callable, str]] = None,
        # scm generator
        scm_generator_class: th.Optional[th.Union[th.Type, str]] = None,
        scm_generator_args: th.Optional[th.Dict] = None,
        # validation (split from train)
        val_size: th.Optional[th.Union[int, float]] = None,
        # seed
        seed: int = 0,
        # dataloader
        dl_args: th.Optional[th.Dict[str, th.Any]] = None,
        train_dl_args: th.Optional[th.Dict[str, th.Any]] = None,
        val_dl_args: th.Optional[th.Dict[str, th.Any]] = None,
        # batch_size
        batch_size: th.Optional[int] = 16,
        train_batch_size: th.Optional[int] = None,
        val_batch_size: th.Optional[int] = None,
        # pin memory
        pin_memory: bool = True,
        train_pin_memory: th.Optional[bool] = None,
        val_pin_memory: th.Optional[bool] = None,
        # shuffle
        train_shuffle: bool = True,
        val_shuffle: bool = False,
        # num_workers
        num_workers: int = 0,
        train_num_workers: th.Optional[int] = None,
        val_num_workers: th.Optional[int] = None,
        # extra parameters (ignored)
        **kwargs,
    ):
        """
        Datamodule for causal datasets.

        Args:
            observation_size: size of the observational data
            intervention_size: size of the interventional data (if 0, only observational data is used)
            val_size: size of the validation set (if 0 or None, no validation set is used)
            seed: seed for random_split
            dl_args: arguments to pass to DataLoader
            train_dl_args: arguments to pass to DataLoader for train set (overrides dl_args)
            val_dl_args: arguments to pass to DataLoader for val set (overrides dl_args)
            batch_size: batch size for train and val
            train_batch_size: batch size for train (overrides batch_size)
            val_batch_size: batch size for val (overrides batch_size)
            pin_memory: pin memory for train and val
            train_pin_memory: pin memory for train (overrides pin_memory)
            val_pin_memory: pin memory for val (overrides pin_memory)
            train_shuffle: shuffle for train
            val_shuffle: shuffle for val
            num_workers: num_workers for train and val
            train_num_workers: num_workers for train (overrides num_workers)
            val_num_workers: num_workers for val (overrides num_workers)
        """
        super().__init__()
        # seed
        self.seed = seed

        # data
        self.observation_size = observation_size

        # SCM generator
        # setup the class
        self.scm_generator_class = scm_generator_class
        if self.scm_generator_class is not None:
            self.scm_generator_class = dycode.eval(self.scm_generator_class) if isinstance(
                self.scm_generator_class, str) else self.scm_generator_class
        # setup the class constructor arguments
        self.scm_generator_args = scm_generator_args if scm_generator_args is not None else {}
        self.generator = None

        # interventions
        self.interventional_episode_size = interventional_episode_size
        self.interventional_episode_count = interventional_episode_count
        self.intervention_function = intervention_function
        if self.intervention_function is not None:
            self.intervention_function = dycode.eval(self.intervention_function) if isinstance(
                self.intervention_function, str) else self.intervention_function

        # training and validation
        self.train_data, self.val_data = None, None
        self.train_dl_args = train_dl_args or dl_args or {}
        self.val_dl_args = val_dl_args or dl_args or {}
        self.val_size = val_size
        assert (
            val_size is None or isinstance(val_size, int) or (
                isinstance(val_size, float) and val_size < 1)
        ), "invalid validation size is provided (either int, float (between zero and one) or None)"

        # batch_size
        self.train_batch_size = train_batch_size if train_batch_size is not None else batch_size
        self.val_batch_size = val_batch_size if val_batch_size is not None else batch_size
        assert self.train_batch_size or self.val_batch_size, "at least one of batch_sizes should be a positive number"
        # pin memory
        self.train_pin_memory = train_pin_memory if train_pin_memory is not None else pin_memory
        self.val_pin_memory = val_pin_memory if val_pin_memory is not None else pin_memory
        # shuffle
        self.train_shuffle, self.val_shuffle = (
            train_shuffle,
            val_shuffle,
        )
        # num_workers
        self.train_num_workers = train_num_workers if train_num_workers is not None else num_workers
        self.val_num_workers = val_num_workers if val_num_workers is not None else num_workers

    def generate_datasets(self) -> th.List[OCDDataset]:
        """
        Generate the datasets (interventional and observational)

        Returns:
            list of datasets where the first one is observational and the rest is interventional
        """
        # create a generator if it is the first time
        if self.generator is None:
            self.generator = self.scm_generator_class(
                seed=self.seed, **self.scm_generator_args)

        # create an scm from the generator
        scm = self.generator.generate_scm()

        # TODO: This should be implemented better
        self.scm = scm

        # simulate the observational dataset
        datasets = [
            OCDDataset(samples=scm.simulate(self.observation_size, seed=self.seed), dag=scm.dag, name='observational')]

        # go for interventional datasets
        if self.interventional_episode_count:
            nodes = list(scm.nodes())

            # permute the nodes according to the seed
            np.random.seed(self.seed)
            nodes = np.random.permutation(nodes)

            assert len(nodes) >= self.interventional_episode_count, "interventional episode size is larger than the number of nodes (try increasing the episode sizes instead)"
            for i in range(self.interventional_episode_count):
                new_dataset = OCDDataset(
                    samples=scm.simulate(
                        self.interventional_episode_size, seed=self.seed+i+1,
                        intervention_node=nodes[i], intervention_function=self.intervention_function),
                    dag=scm.dag,
                    intervention_column=nodes[i],
                    name=f'interventional_{i}'
                )
                datasets.append(new_dataset)

        return datasets

    def setup(self, stage: th.Optional[str] = None) -> None:
        """
        Setup the data for training and validation. this method is called on every GPU in distributed training.

        Args:
            stage: stage to setup (train, val, test, None), see pytorch_lightning docs for more details

        Returns:
            None
        """
        if (stage == "fit" or stage == "tune") and self.train_batch_size:
            # generate the datasets (interventional and observational)
            datasets = self.generate_datasets()
            self.datasets = datasets
            if not self.val_size:
                # setup train data only (no validation)
                self.train_data = datasets
                self.val_data = None
            if self.val_size and self.val_batch_size:
                # iterate over datasets and split them into train and val
                self.train_data, self.val_data = [], []
                prng = torch.Generator()
                prng.manual_seed(self.seed)  # for reproducibility
                for dataset in datasets:
                    train_len = (
                        len(dataset) - self.val_size
                        if isinstance(self.val_size, int)
                        else int(len(dataset) * (1 - self.val_size))
                    )
                    train_data, val_data = random_split(
                        dataset, [train_len, len(dataset) - train_len], generator=prng)
                    self.train_data.append(train_data)
                    self.val_data.append(val_data)

        elif stage == "test" and self.test_batch_size:
            raise NotImplementedError("test stage is not implemented yet")

    def get_dataloader(
        self,
        name: str,
        batch_size: th.Optional[int] = None,
        shuffle: th.Optional[bool] = None,
        num_workers: th.Optional[int] = None,
        pin_memory: th.Optional[bool] = None,
        **params,
    ):
        # TODO: should fix this such that instead of a list of dataloaders, it merges everything into one
        # dataloader but you will have the ability to choose which dataset to use
        """
        Generic function to create dataloaders for train and val sets.

        Args:
            name: name of the dataset (train or val)
            batch_size: batch size (if None, self.{name}_batch_size is used)
            shuffle: shuffle (if None, self.{name}_shuffle is used)
            num_workers: num_workers (if None, self.{name}_num_workers is used)
            pin_memory: pin_memory (if None, self.{name}_pin_memory is used)
            params: extra parameters to pass to DataLoader (overrides self.{name}_dl_args)
        """
        data = getattr(self, f"{name}_data")
        if data is None:
            # no data is available for this stage so don't create a dataloader
            return None
        # setup data loader args
        dl_args = getattr(self, f"{name}_dl_args", {})
        dl_args.update(params)  # override with extra params

        batch_size = batch_size if batch_size is not None else getattr(
            self, f"{name}_batch_size")
        shuffle = shuffle if shuffle is not None else getattr(
            self, f"{name}_shuffle")
        num_workers = num_workers if num_workers is not None else getattr(
            self, f"{name}_num_workers")
        pin_memory = pin_memory if pin_memory is not None else getattr(
            self, f"{name}_pin_memory")
        # create dataloaders
        data_loaders = [
            DataLoader(
                dataset,
                batch_size=batch_size,
                shuffle=shuffle,
                num_workers=num_workers,
                pin_memory=pin_memory,
                **dl_args,
            )
            for dataset in (data if isinstance(data, list) else [data])
        ]
        # according to pytorch-lightning docs, this should be a list of dataloaders or a dict of dataloaders
        # https://pytorch-lightning.readthedocs.io/en/stable/guides/data.html#return-multiple-dataloaders
        return (
            data_loaders if len(data_loaders) > 1 else data_loaders[0]
        )  # todo: might need to change it to a dict later on

    def train_dataloader(self, **params):
        return self.get_dataloader("train", **params)

    def val_dataloader(self, **params):
        return self.get_dataloader("val", **params)
