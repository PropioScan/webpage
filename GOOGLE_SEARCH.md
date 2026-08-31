# Publishing Propioscan in Google Search

Propioscan is publicly available at `https://propioscan.com/`; there is no separate
upload to Google. Google must be allowed to crawl the page, and the domain owner
should register it in Google Search Console.

## One-time Search Console setup

1. Open <https://search.google.com/search-console> while signed in to the Google
   account that should own the property.
2. Add a **Domain** property named `propioscan.com`.
3. Copy the TXT verification value shown by Google.
4. In cPanel, open **Zone Editor**, choose **Manage** for `propioscan.com`, and add
   a TXT record:
   - Name: `propioscan.com`
   - Type: `TXT`
   - Record: the exact `google-site-verification=...` value from Search Console
5. Return to Search Console and select **Verify**. DNS propagation can take time;
   leave the TXT record in place after verification.
6. Open **Sitemaps**, enter `sitemap.xml`, and submit it. The live sitemap URL is
   <https://propioscan.com/sitemap.xml>.
7. Open **URL inspection**, inspect `https://propioscan.com/`, run the live test,
   and select **Request indexing** once.

Do not repeatedly request indexing. Search Console reports whether Google can
crawl and index the page; it does not guarantee a particular ranking or an
immediate appearance in results.

## SEO features implemented in the application

- One canonical HTTPS URL and Slovenian language declaration
- Search-focused title and description that match visible page content
- Google crawler directives allowing the homepage to be indexed
- Open Graph and Twitter/X preview metadata with an absolute image URL
- JSON-LD for the Propioscan organization, website, and free software application
- Root-level `robots.txt` and `sitemap.xml`
- `noindex` response headers for API/admin traffic and exclusion from the sitemap
- Visible descriptions of the parcel analysis instead of hidden keyword text

After changing the homepage content substantially, update the `<lastmod>` date in
the `sitemap()` response in `app/main.py`, deploy, and let Search Console recrawl it.

## Ongoing work that improves ranking

- Publish original, useful Slovenian guides on their own indexable URLs for topics
  such as parcel lookup, intended land use, spatial plans, and official location
  information. Each page should answer a real user question and link naturally to
  the parcel checker.
- Earn relevant links and mentions from legitimate real-estate, architecture,
  surveying, municipal, or professional sites. Never buy bulk or automated links.
- Keep the homepage fast and mobile-friendly, and monitor Core Web Vitals and page
  indexing in Search Console.
- Review the queries and pages in Search Console monthly, then improve content for
  relevant searches where the page is already receiving impressions.
- Keep titles, headings, structured data, and visible claims accurate. Do not add a
  `meta keywords` tag; Google Search does not use it for ranking.

Useful validation tools:

- Search Console URL inspection: <https://search.google.com/search-console>
- Rich Results Test: <https://search.google.com/test/rich-results>
- PageSpeed Insights: <https://pagespeed.web.dev/>
