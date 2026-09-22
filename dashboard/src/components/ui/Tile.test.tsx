import { render, screen } from '@testing-library/react';
import { Tile } from './Tile';

it('renders value and a signed percentage delta with an arrow', () => {
  render(<Tile label="Estimated cost" value="$573" delta={{ abs: 60, pct: 0.12, dir: 'up' }} upIsBad />);
  expect(screen.getByText('$573')).toBeInTheDocument();
  const d = screen.getByTestId('delta');
  expect(d).toHaveTextContent('+12%');
  expect(d.querySelector('svg')).not.toBeNull();
  expect(d.className).toMatch(/crit/);
});

it('uses neutral colour when up is not bad and says "new" when there was nothing before', () => {
  // REGRESSION (H8): with a prior value of 0 the tile printed the raw
  // difference, e.g. "+55.413754999999995" for a cost.
  render(<Tile label="Estimated cost" value="$55.41" delta={{ abs: 55.413754999999995, pct: null, dir: 'up' }} />);
  const d = screen.getByTestId('delta');
  expect(d).toHaveTextContent('new');
  expect(d).not.toHaveTextContent('55.41375');
  expect(d.className).not.toMatch(/crit|good/);
});

it('shows no change text when both windows are zero', () => {
  render(<Tile label="Tokens" value="0" delta={{ abs: 0, pct: null, dir: 'flat' }} />);
  expect(screen.getByTestId('delta')).toHaveTextContent('0%');
});

it('omits the delta entirely when null', () => {
  render(<Tile label="Tokens" value="1" delta={null} />);
  expect(screen.queryByTestId('delta')).toBeNull();
});
