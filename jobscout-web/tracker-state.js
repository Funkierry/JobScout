// Cursor navigation is independent from DOM rendering and session ownership.
(function (root, factory) {
  if (typeof module !== "undefined") module.exports = factory();
  else root.JobScoutTracker = factory();
})(globalThis, function () {
  class TrackerPager {
    constructor(limit = 50) {
      this.limit = limit;
      this.reset();
    }
    reset() {
      this.cursors = [null];
      this.stage = null;
      this.data = null;
      this.committed = null;
    }
    filter(stage) {
      this.stage = stage;
      this.cursors = [null];
    }
    next() {
      if (!this.data?.has_more || !Number.isInteger(this.data.next_before_id))
        return false;
      this.cursors.push(this.data.next_before_id);
      return true;
    }
    previous() {
      if (this.cursors.length < 2) return false;
      this.cursors.pop();
      return true;
    }
    get page() {
      return this.cursors.length;
    }
    url() {
      const query = new URLSearchParams({ limit: String(this.limit) });
      const before = this.cursors.at(-1);
      if (before !== null) query.set("before_id", String(before));
      if (this.stage !== null) query.set("stage", this.stage);
      return `/api/jobscout/tracker/applications/page?${query}`;
    }
    accept(data) {
      if (
        !data ||
        !Array.isArray(data.items) ||
        !data.summary ||
        !Array.isArray(data.stages)
      )
        throw new Error("投递列表返回格式有误，请刷新重试。");
      this.data = data;
      this.committed = { cursors: [...this.cursors], stage: this.stage };
      return data.items;
    }
    reject() {
      if (!this.committed) return;
      this.cursors = [...this.committed.cursors];
      this.stage = this.committed.stage;
    }
  }
  return { TrackerPager };
});
