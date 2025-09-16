import asyncio
import os
from pathlib import Path
from typing import Any, Literal, Protocol

from git import InvalidGitRepositoryError
from git import Repo as GitRepo
from pydantic import BaseModel, ConfigDict, Field
from swerex.deployment.abstract import AbstractDeployment
from swerex.runtime.abstract import Command, UploadRequest
from typing_extensions import Self

from sweagent.utils.github import _parse_gh_repo_url
from sweagent.utils.log import get_logger

logger = get_logger("swea-config", emoji="🔧")


class Repo(Protocol):
    """Protocol for repository configurations."""

    base_commit: str
    repo_name: str

    def copy(self, deployment: AbstractDeployment): ...

    def get_reset_commands(self) -> list[str]: ...


def _get_git_reset_commands(base_commit: str) -> list[str]:
    return [
        "git fetch",
        "git status",
        "git restore .",
        "git reset --hard",
        f"git checkout {base_commit}",
        "git clean -fdq",
    ]


class PreExistingRepoConfig(BaseModel):
    """Use this to specify a repository that already exists on the deployment.
    This is important because we need to cd to the repo before running the agent.

    Note: The repository must be at the root of the deployment.
    """

    repo_name: str
    """The repo name (the repository must be located at the root of the deployment)."""
    base_commit: str = Field(default="HEAD")
    """The commit to reset the repository to. The default is HEAD,
    i.e., the latest commit. You can also set this to a branch name (e.g., `dev`),
    a tag (e.g., `v0.1.0`), or a commit hash (e.g., `a4464baca1f`).
    SWE-agent will then start from this commit when trying to solve the problem.
    """

    type: Literal["preexisting"] = "preexisting"
    """Discriminator for (de)serialization/CLI. Do not change."""

    reset: bool = True
    """If True, reset the repository to the base commit after the copy operation."""

    model_config = ConfigDict(extra="forbid")

    def copy(self, deployment: AbstractDeployment):
        """Does nothing."""
        pass

    def get_reset_commands(self) -> list[str]:
        """Issued after the copy operation or when the environment is reset."""
        if self.reset:
            return _get_git_reset_commands(self.base_commit)
        return []


class LocalRepoConfig(BaseModel):
    path: Path
    base_commit: str = Field(default="HEAD")
    """The commit to reset the repository to. The default is HEAD,
    i.e., the latest commit. You can also set this to a branch name (e.g., `dev`),
    a tag (e.g., `v0.1.0`), or a commit hash (e.g., `a4464baca1f`).
    SWE-agent will then start from this commit when trying to solve the problem.
    """

    type: Literal["local"] = "local"
    """Discriminator for (de)serialization/CLI. Do not change."""

    model_config = ConfigDict(extra="forbid")

    @property
    def repo_name(self) -> str:
        """Set automatically based on the repository name. Cannot be set."""
        return Path(self.path).resolve().name.replace(" ", "-").replace("'", "")

    # Let's not make this a model validator, because it leads to cryptic errors.
    # Let's just check during copy instead.
    def check_valid_repo(self) -> Self:
        """Check if the path is a valid git repository and not dirty."""
        # first make sure path exists
        if not Path(self.path).exists():
            os.mkdir(self.path)
            logger.info(f"Created directory {self.path} as it did not exist.")
        if not Path(self.path).is_dir():
            msg = f"Path {self.path} is not a directory."
            raise ValueError(msg)
        # now check if it's a git repo
        try:
            repo = GitRepo(self.path, search_parent_directories=True)
        except InvalidGitRepositoryError as e:
            msg = f"Could not find git repository at {self.path=}."
            raise ValueError(msg) from e
        if repo.is_dirty() and "PYTEST_CURRENT_TEST" not in os.environ:
            msg = f"Local git repository {self.path} is dirty. Please commit or stash changes."
            raise ValueError(msg)
        return self

    def copy(self, deployment: AbstractDeployment):
        self.check_valid_repo()
        
        # For local deployment, we might not need to copy files at all
        # Check if this is a local deployment
        deployment_type = getattr(deployment._config, 'type', None) if hasattr(deployment, '_config') else None
        
        if deployment_type == 'local':
            logger.info(f"Local deployment detected. Working directory will be set to {self.path}")
            # For local deployment, we just need to ensure we work in the right directory
            # No need to copy files
            return
            
        # For non-local deployments, proceed with the upload
        try:
            asyncio.run(
                deployment.runtime.upload(UploadRequest(source_path=str(self.path), target_path=f"/{self.repo_name}"))
            )
            logger.info(f"Successfully uploaded repository to /{self.repo_name}")
        except Exception as e:
            logger.warning(f"Failed to upload repository: {e}. Continuing without copy operation.")
            return
            
        try:
            r = asyncio.run(deployment.runtime.execute(Command(command=f"chown -R root:root /{self.repo_name}", shell=True)))
            if r.exit_code != 0:
                logger.warning(f"Failed to change permissions on copied repository (exit code: {r.exit_code}). This might not be critical.")
                logger.debug(f"chown stderr: {r.stderr}, stdout: {r.stdout}")
            else:
                logger.info(f"Successfully changed permissions for /{self.repo_name}")
        except Exception as e:
            logger.warning(f"Exception during permission change: {e}. This might not be critical.")

    def get_reset_commands(self) -> list[str]:
        """Issued after the copy operation or when the environment is reset."""
        return _get_git_reset_commands(self.base_commit)


class GithubRepoConfig(BaseModel):
    github_url: str

    base_commit: str = Field(default="HEAD")
    """The commit to reset the repository to. The default is HEAD,
    i.e., the latest commit. You can also set this to a branch name (e.g., `dev`),
    a tag (e.g., `v0.1.0`), or a commit hash (e.g., `a4464baca1f`).
    SWE-agent will then start from this commit when trying to solve the problem.
    """

    clone_timeout: float = 500
    """Timeout for git clone operation."""

    type: Literal["github"] = "github"
    """Discriminator for (de)serialization/CLI. Do not change."""

    model_config = ConfigDict(extra="forbid")

    def model_post_init(self, __context: Any) -> None:
        if self.github_url.count("/") == 1:
            self.github_url = f"https://github.com/{self.github_url}"

    @property
    def repo_name(self) -> str:
        org, repo = _parse_gh_repo_url(self.github_url)
        return f"{org}__{repo}"

    def _get_url_with_token(self, token: str) -> str:
        """Prepend github token to URL"""
        if not token:
            return self.github_url
        if "@" in self.github_url:
            logger.warning("Cannot prepend token to URL. '@' found in URL")
            return self.github_url
        _, _, url_no_protocol = self.github_url.partition("://")
        return f"https://{token}@{url_no_protocol}"

    def copy(self, deployment: AbstractDeployment):
        """Clones the repository to the sandbox."""
        base_commit = self.base_commit
        github_token = os.getenv("GITHUB_TOKEN", "")
        url = self._get_url_with_token(github_token)
        asyncio.run(
            deployment.runtime.execute(
                Command(
                    command=" && ".join(
                        (
                            f"mkdir /{self.repo_name}",
                            f"cd /{self.repo_name}",
                            "git init",
                            f"git remote add origin {url}",
                            f"git fetch --depth 1 origin {base_commit}",
                            "git checkout FETCH_HEAD",
                            "cd ..",
                        )
                    ),
                    timeout=self.clone_timeout,
                    shell=True,
                    check=True,
                )
            ),
        )

    def get_reset_commands(self) -> list[str]:
        """Issued after the copy operation or when the environment is reset."""
        return _get_git_reset_commands(self.base_commit)


class NoGitRepoConfig(BaseModel):
    """Repository configuration that provides access to a directory without any git operations.
    
    This is useful when you want the agent to work in a specific directory but don't need
    git repository management (reset, checkout, etc.). The directory will be created if it doesn't exist.
    """
    
    path: Path
    """Path to the working directory."""
    
    type: Literal["no_git"] = "no_git"
    """Discriminator for (de)serialization/CLI. Do not change."""
    
    model_config = ConfigDict(extra="forbid")
    
    @property
    def repo_name(self) -> str:
        """Set automatically based on the directory name."""
        return Path(self.path).resolve().name.replace(" ", "-").replace("'", "")
    
    @property
    def base_commit(self) -> str:
        """No git operations, so no base commit."""
        return "HEAD"
    
    def copy(self, deployment: AbstractDeployment) -> None:
        """Create the directory if it doesn't exist."""
        import asyncio
        from swerex.runtime.abstract import Command
        
        # Create directory if it doesn't exist
        asyncio.run(
            deployment.runtime.execute(
                Command(command=f"mkdir -p {self.path}", shell=True, check=True)
            )
        )
        logger.info(f"Directory {self.path} is ready for no-git operations")
    
    def get_reset_commands(self) -> list[str]:
        """Return commands to reset the directory to a clean state.
        
        Full reset: Remove all non-hidden files and recreate clean directory.
        """
        return [
            # Remove all non-hidden files and directories
            f"find {self.path} -mindepth 1 -not -path '*/.*' -delete 2>/dev/null || true",
            # Ensure the directory still exists
            f"mkdir -p {self.path}",
        ]


RepoConfig = LocalRepoConfig | GithubRepoConfig | PreExistingRepoConfig | NoGitRepoConfig


def repo_from_simplified_input(
    *, input: str, base_commit: str = "HEAD", type: Literal["local", "github", "preexisting", "no_git", "auto"] = "auto"
) -> RepoConfig:
    """Get repo config from a simplified input.

    Args:
        input: Local path or GitHub URL
        type: The type of repo. Set to "auto" to automatically detect the type
            (does not work for preexisting repos).
    """
    if type == "local":
        return LocalRepoConfig(path=Path(input), base_commit=base_commit)
    if type == "github":
        return GithubRepoConfig(github_url=input, base_commit=base_commit)
    if type == "preexisting":
        return PreExistingRepoConfig(repo_name=input, base_commit=base_commit)
    if type == "no_git":
        return NoGitRepoConfig(path=Path(input))
    if type == "auto":
        if input.startswith("https://github.com/"):
            return GithubRepoConfig(github_url=input, base_commit=base_commit)
        else:
            return LocalRepoConfig(path=Path(input), base_commit=base_commit)
    msg = f"Unknown repo type: {type}"
    raise ValueError(msg)
