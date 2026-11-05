# telegram-core recipe

The Telegram bot runs on `singnet/Omega` `main` plus core changes that are
still open upstream. This branch holds only the recipe for that core:

- `branches.txt`: the branches of this repo merged onto `main`, in order.
- `merge.sh`: makes the merged core in a fresh checkout. Run it locally with
  `./merge.sh branches.txt /tmp/core`.
- `.github/workflows/telegram-core.yml`: on every push to this branch, merges,
  tests and publishes the result as branch `telegram-core` and a
  `telegram-core-<date>-<sha>` tag.

To add a core change, push its branch here and add it to `branches.txt`. To
pick up a newer upstream `main`, push an empty commit. A branch drops out once
its upstream PR is merged. A conflict fails the run and names the branch;
rebase that branch onto `main` and push it.

Staging builds from `telegram-core`. Production pins `CORE_REF` on
singnet/Omega_CustomBots to a tagged `telegram-core` commit that passed staging.
