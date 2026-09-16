/*
 * Fantasy weeks, shared by every page that numbers them.
 *
 * The standard weeks come from /schedules/api/weeks: Monday to Sunday, with a
 * week gutted by a league-wide break folded into its neighbour the way Yahoo
 * does it (Week 19 of 2026-27 runs Feb 1-14 across the All-Star break).
 * A league that departs from that can edit its weeks on the Lineups page, and
 * the edit is kept here - in localStorage under `fs_fantasyWeeks` - so draft
 * prep's playoff weeks, the Schedules page and Lineups all count weeks the
 * same way.
 *
 * Edits are saved against the season they were made for. A new season's
 * schedule starts on a different date, so last year's custom weeks are
 * ignored rather than misapplied. They also never override a synced league's
 * own weeks, which are the real ones.
 *
 * The edit operations are pure: each takes a week list and returns a new one,
 * contiguous from the first night of the season to the last and renumbered
 * from 1. Nothing here can leave a gap or an overlap.
 *
 * Author - Jason Druckenmiller
 * Created - 9/16/2026
 * Updated - 9/16/2026
 */
(function () {
    const KEY = 'fs_fantasyWeeks';
    const DAY_MS = 86400000;

    const toTime = iso => Date.parse(`${iso}T00:00:00Z`);
    const addDays = (iso, n) => new Date(toTime(iso) + n * DAY_MS).toISOString().slice(0, 10);
    const length = week => Math.round((toTime(week.end) - toTime(week.start)) / DAY_MS) + 1;

    function renumber(weeks) {
        return weeks.map((w, i) => ({ week: i + 1, start: w.start, end: w.end }));
    }

    function isValid(weeks, season) {
        if (!Array.isArray(weeks) || !weeks.length) return false;
        if (weeks[0].start !== season.start || weeks[weeks.length - 1].end !== season.end) return false;
        return weeks.every((w, i) =>
            typeof w.start === 'string' && typeof w.end === 'string' && w.start <= w.end
            && (i === 0 || w.start === addDays(weeks[i - 1].end, 1)));
    }

    function readCustom(season) {
        try {
            const saved = JSON.parse(localStorage.getItem(KEY));
            if (!saved || saved.season !== season.start || !isValid(saved.weeks, season)) return null;
            return renumber(saved.weeks);
        } catch (error) {
            return null;
        }
    }

    /**
     * { season, source, standard, weeks, customised }. `weeks` is what a page
     * should use; `standard` is kept so an editor can show what changed.
     */
    async function load() {
        const response = await fetch('/schedules/api/weeks');
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || 'Could not load fantasy weeks.');

        const custom = data.source === 'derived' ? readCustom(data.season) : null;
        return {
            season: data.season,
            source: data.source,
            standard: data.weeks,
            weeks: custom || data.weeks,
            customised: Boolean(custom),
        };
    }

    /** Save an edited list. Returns false if the browser refused the write. */
    function save(season, weeks) {
        try {
            localStorage.setItem(KEY, JSON.stringify({
                season: season.start,
                weeks: weeks.map(w => ({ start: w.start, end: w.end })),
            }));
            return true;
        } catch (error) {
            return false;
        }
    }

    function reset() {
        try {
            localStorage.removeItem(KEY);
        } catch (error) {
            /* nothing saved, nothing to clear */
        }
    }

    /**
     * Move week `index`'s last night to `end`. The next week starts the day
     * after, growing or shrinking to fit; any week the new end swallows whole
     * is absorbed. The last week always ends with the season, so it cannot be
     * moved. Returns null for an end before the week's own start.
     */
    function setEnd(weeks, index, end, season) {
        const list = weeks.map(w => ({ start: w.start, end: w.end }));
        if (index < 0 || index >= list.length - 1) return renumber(list);
        if (!end || end < list[index].start) return null;

        const capped = end > season.end ? season.end : end;
        list[index].end = capped;
        while (index + 1 < list.length && list[index + 1].end <= capped) {
            list.splice(index + 1, 1);
        }
        if (index + 1 < list.length) {
            list[index + 1].start = addDays(capped, 1);
        } else {
            list[index].end = season.end;
        }
        return renumber(list);
    }

    /** Fold the week after `index` into it. */
    function merge(weeks, index) {
        const list = weeks.map(w => ({ start: w.start, end: w.end }));
        if (index < 0 || index >= list.length - 1) return renumber(list);
        list[index].end = list[index + 1].end;
        list.splice(index + 1, 1);
        return renumber(list);
    }

    /**
     * Cut a week longer than seven days at its first Sunday - undoing a merge,
     * since fantasy weeks end on a Sunday. Shorter weeks come back unchanged.
     */
    function split(weeks, index) {
        const list = weeks.map(w => ({ start: w.start, end: w.end }));
        const week = list[index];
        if (!week || length(week) <= 7) return renumber(list);

        let cut = week.start;
        while (new Date(toTime(cut)).getUTCDay() !== 0) cut = addDays(cut, 1);
        if (cut >= week.end) cut = addDays(week.start, 6);

        list.splice(index + 1, 0, { start: addDays(cut, 1), end: week.end });
        week.end = cut;
        return renumber(list);
    }

    window.FantasyWeeks = { load, save, reset, setEnd, merge, split, length, isValid };
})();
