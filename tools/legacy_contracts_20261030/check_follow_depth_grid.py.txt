"""沿截图工作区前后两层实测鼠标目标；继承同一运动/限速/回中验收。"""
import unittest
import check_follow_plane_grid as grid


class DepthGrid(grid.PlaneGrid):
    directions=[(270,(-600,0,650))]

    def points(self):
        return [(u,v,depth) for depth in (0.,1.) for v in (.25,.5,.75)
                for u in ((.25,.5,.75) if v!=.5 else (.75,.5,.25))] + [(.5,.5,.5)]


if __name__=='__main__':unittest.main(verbosity=2)
