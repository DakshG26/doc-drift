# Worker CLI Reference

The worker CLI coordinates background execution tasks.

## Configuration Options

To configure the worker, supply command line arguments during launch:

- You can specify `--timeout` in seconds to terminate stalled worker jobs before failure.
- Default execution timeout is set to 30 seconds across all worker pools.
