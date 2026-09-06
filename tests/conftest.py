"""让目录分页替身能响应活动面板内的翻页脚本。"""


def pytest_configure(config) -> None:
    del config
    from test_publish_writer import PagedCatalogPage

    original_evaluate = PagedCatalogPage.evaluate

    def evaluate(self, script: str, *args: object) -> object:
        if "click-catalog-page" in script:
            number = int(args[0]) if args else 0
            if number not in self.pages or self.hide_pager:
                return False
            if number in self.stuck_pages:
                return True
            self.current = number
            self.visited.append(number)
            if self.row_lag_timeouts:
                self._rows_catch_up_at = self.timeouts + self.row_lag_timeouts
            else:
                self.displayed = number
            return True
        return original_evaluate(self, script, *args)

    PagedCatalogPage.evaluate = evaluate
