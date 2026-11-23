# Sturdier passport browser tests

**Maintenance**

- The passport browser tests wait for an airfield's page to finish loading
  after clicking it in a passport, instead of failing on a slow CI runner.
